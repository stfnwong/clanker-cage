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

    // In main(), before binding:
    eprintln!("Attempting to bind to {}", socket_path.display());

    let listener = match UnixListener::bind(&socket_path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("Bind failed: {}", e);
            return Err(e.into());
        }
    };

    eprintln!("Bound successfully, accepting connections...");

    
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
    let mut buffer = Vec::new();
    let mut temp = [0u8; 4096];
    
    // ─── Step 1: Read until we see \r\n\r\n (end of headers) ──────
    loop {
        let n = stream.read(&mut temp).await?;
        if n == 0 {
            return Err("Connection closed while reading headers".into());
        }
        buffer.extend_from_slice(&temp[..n]);
        
        // Check for end of headers
        if buffer.windows(4).any(|w| w == b"\r\n\r\n") {
            break;
        }
        
        // Safety limit: don't read forever
        if buffer.len() > 64 * 1024 {
            return Err("Headers too large".into());
        }
    }
    
    // ─── Step 2: Parse Content-Length from headers ────────────────
    let header_end = buffer.windows(4).position(|w| w == b"\r\n\r\n").unwrap() + 4;
    let headers = String::from_utf8_lossy(&buffer[..header_end]);
    
    let content_length = headers
        .lines()
        .find(|line| line.to_lowercase().starts_with("content-length:"))
        .and_then(|line| line.split(':').nth(1))
        .and_then(|s| s.trim().parse::<usize>().ok())
        .unwrap_or(0);
    
    eprintln!("Headers parsed. Content-Length: {}", content_length);
    
    // ─── Step 3: Read the body (exactly Content-Length bytes) ─────
    let mut body = vec![0u8; content_length];
    let mut bytes_read = 0;
    let body_start = header_end;
    
    // We might have already read some of the body into buffer
    if buffer.len() > body_start {
        let available = buffer.len() - body_start;
        let to_copy = available.min(content_length);
        body[..to_copy].copy_from_slice(&buffer[body_start..body_start + to_copy]);
        bytes_read = to_copy;
    }
    
    // Read the rest of the body
    while bytes_read < content_length {
        let n = stream.read(&mut body[bytes_read..]).await?;
        if n == 0 {
            return Err("Connection closed while reading body".into());
        }
        bytes_read += n;
    }
    
    eprintln!("Body read: {} bytes", bytes_read);
    
    // ─── Step 4: Parse the JSON body ──────────────────────────────
    let request: Value = serde_json::from_slice(&body)?;
    let mut request_body = request.get("body").cloned().unwrap_or_else(|| json!({}));
    
    // Inject model if not specified
    if request_body.get("model").is_none() {
        request_body["model"] = json!(default_model);
    }
    
    // ─── Step 5: Forward to DeepSeek ──────────────────────────────
    eprintln!("Forwarding to DeepSeek...");
    
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/chat/completions", BASE_URL))
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&request_body)
        .send()
        .await?;
    
    eprintln!("Got response: status {}", response.status());
    
    // ─── Step 6: Send response back ───────────────────────────────
    let status = response.status().as_u16();
    let response_bytes = response.bytes().await?;
    
    eprintln!("Response body: {} bytes", response_bytes.len());
    
    // Write HTTP response with proper headers
    stream.write_all(format!("HTTP/1.1 {}\r\n", status).as_bytes()).await?;
    stream.write_all(b"Content-Type: application/json\r\n").await?;
    stream.write_all(b"Content-Length: ").await?;
    stream.write_all(response_bytes.len().to_string().as_bytes()).await?;
    stream.write_all(b"\r\n\r\n").await?;
    stream.write_all(&response_bytes).await?;
    stream.flush().await?;
    
    eprintln!("Response sent");
    
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
