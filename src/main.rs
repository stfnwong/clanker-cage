// provider-proxy.rs
// Host-side Unix socket proxy for LLM API calls.
// Holds API keys. Forwards to DeepSeek. Keeps keys out of containers.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use serde_json::{json, Value};

const SOCKET_PATH: &str = "~/.cache/clanker/provider.sock";
const BASE_URL: &str = "https://api.deepseek.com/v1";
const DEFAULT_MODEL: &str = "deepseek-chat";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let socket_path = expand_tilde(SOCKET_PATH)?;
    
    // Clean up stale socket
    if socket_path.exists() {
        fs::remove_file(&socket_path)?;
    }
    
    // Ensure parent directory exists
    if let Some(parent) = socket_path.parent() {
        fs::create_dir_all(parent)?;
    }
    
    // Get API key from environment
    let api_key = std::env::var("DEEPSEEK_API_KEY")
        .expect("DEEPSEEK_API_KEY not set");
    let model = std::env::var("CLANKER_MODEL").unwrap_or_else(|_| DEFAULT_MODEL.to_string());
    
    // Create Unix socket listener
    let listener = UnixListener::bind(&socket_path)?;
    
    // Set permissions so only the user can access it
    let metadata = fs::metadata(&socket_path)?;
    let mut perms = metadata.permissions();
    perms.set_mode(0o600);
    fs::set_permissions(&socket_path, perms)?;
    
    eprintln!("Provider proxy listening on {}", socket_path.display());
    eprintln!("Model: {}", model);
    
    loop {
        let (mut stream, _) = listener.accept().await?;
        let api_key = api_key.clone();
        let model = model.clone();
        
        tokio::spawn(async move {
            if let Err(e) = handle_client(&mut stream, &api_key, &model).await {
                eprintln!("Client error: {}", e);
            }
        });
    }
}

async fn handle_client(
    stream: &mut UnixStream,
    api_key: &str,
    default_model: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    // Read the entire request
    let mut buffer = Vec::new();
    stream.read_to_end(&mut buffer).await?;
    
    // Parse JSON request
    let request: Value = serde_json::from_slice(&buffer)?;
    let mut body = request.get("body").cloned().unwrap_or_else(|| json!({}));
    
    // Inject model if not specified
    if body.get("model").is_none() {
        body["model"] = json!(default_model);
    }
    
    // Ensure stream is set
    if body.get("stream").is_none() {
        body["stream"] = json!(false);
    }
    
    // Forward to DeepSeek
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/chat/completions", BASE_URL))
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await?;
    
    // Stream response back to client
    let status = response.status();
    let mut resp_bytes = response.bytes().await?;
    
    // Write a minimal HTTP response header + body
    stream.write_all(format!("HTTP/1.1 {}\r\n", status.as_u16()).as_bytes()).await?;
    stream.write_all(b"\r\n").await?;
    stream.write_all(&resp_bytes).await?;
    stream.flush().await?;
    
    Ok(())
}

fn expand_tilde(path: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(stripped) = path.strip_prefix("~/") {
        let home = std::env::var("HOME")?;
        Ok(PathBuf::from(home).join(stripped))
    } else {
        Ok(PathBuf::from(path))
    }
}
