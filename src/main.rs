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
    // ─── Read HTTP headers (until \r\n\r\n) ──────────────────────
    let mut buffer = Vec::new();
    let mut temp = [0u8; 8192];
    
    loop {
        let n = stream.read(&mut temp).await?;
        if n == 0 {
            return Err("Connection closed while reading headers".into());
        }
        buffer.extend_from_slice(&temp[..n]);
        
        // Look for end of headers
        if buffer.windows(4).any(|w| w == b"\r\n\r\n") {
            break;
        }
        
        // Prevent unbounded growth
        if buffer.len() > 1024 * 1024 {
            return Err("Headers too large".into());
        }
    }
    
    // Find where headers end
    let header_end = buffer.windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("No header terminator found")? + 4;
    
    // Parse headers to get Content-Length
    let headers_str = String::from_utf8_lossy(&buffer[..header_end]);
    let content_length = headers_str
        .lines()
        .find(|line| line.to_lowercase().starts_with("content-length:"))
        .and_then(|line| line.split(':').nth(1))
        .and_then(|s| s.trim().parse::<usize>().ok())
        .unwrap_or(0);
    
    eprintln!("Content-Length: {}", content_length);
    
    // ─── Read the body ─────────────────────────────────────────────
    let mut body = Vec::new();
    let body_start = header_end;
    
    // Copy any bytes already read past the headers
    if buffer.len() > body_start {
        body.extend_from_slice(&buffer[body_start..]);
    }
    
    // Read remaining body bytes
    while body.len() < content_length {
        let n = stream.read(&mut temp).await?;
        if n == 0 {
            return Err("Connection closed while reading body".into());
        }
        body.extend_from_slice(&temp[..n]);
    }
    
    eprintln!("Body read: {} bytes", body.len());
    
    // ─── Parse JSON ───────────────────────────────────────────────
    let request: Value = serde_json::from_slice(&body)?;
    //let mut request_body = request.get("body").cloned().unwrap_or_else(|| json!({}));

    let mut request_body = if let Some(body) = request.get("body") {
        // Expected format: {"body": {"messages": [...], "stream": true }}
        body.clone()
    }
    else {
        // Expected format: {"messages": [..], "stream": true}
        request.clone()
    };
    
    // Inject model if not specified
    if request_body.get("model").is_none() {
        request_body["model"] = json!(default_model);
    }
    
    // ─── Forward to DeepSeek ──────────────────────────────────────
    eprintln!("Forwarding to DeepSeek...");
    
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/chat/completions", BASE_URL))
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&request_body)
        .send()
        .await?;
    
    let status = response.status().as_u16();
    let response_bytes = response.bytes().await?;
    
    eprintln!("Got response: {} bytes, status {}", response_bytes.len(), status);
    
    // ─── Write HTTP response back ─────────────────────────────────
    stream.write_all(format!("HTTP/1.1 {}\r\n", status).as_bytes()).await?;
    stream.write_all(b"Content-Type: application/json\r\n").await?;
    stream.write_all(format!("Content-Length: {}\r\n", response_bytes.len()).as_bytes()).await?;
    stream.write_all(b"Connection: close\r\n").await?;
    stream.write_all(b"\r\n").await?;
    stream.write_all(&response_bytes).await?;
    stream.flush().await?;
    
    eprintln!("Response sent successfully");
    
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
