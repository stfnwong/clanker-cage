// provider-proxy.rs — Cross-platform provider proxy for clanker

use anyhow::{anyhow, Result};
use std::env;
use std::path::PathBuf;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, UnixListener};
use serde_json::{json, Value};

use std::os::unix::fs::PermissionsExt;


const DEFAULT_PORT: u16 = 11434;
const DEFAULT_SOCKET: &str = "~/.cache/clanker/provider.sock";
const BASE_URL: &str = "https://api.deepseek.com/v1";
const DEFAULT_MODEL: &str = "deepseek-chat";


fn expand_tilde(path: &str) -> PathBuf {
    if let Some(stripped) = path.strip_prefix("~/") {
        let home = env::var("HOME").expect("HOME not set");
        PathBuf::from(home).join(stripped)
    } else {
        PathBuf::from(path)
    }
}


async fn handle_client<S>(stream: &mut S, api_key: &str, default_model: &str) -> Result<()>
where
    S: AsyncReadExt + AsyncWriteExt + Unpin,
{
    // Read HTTP headers
    let mut buffer = Vec::new();
    let mut temp = [0u8; 8192];
    let header_end;
    loop {
        let n = stream.read(&mut temp).await?;
        if n == 0 {
            return Err(anyhow!("Connection closed while reading headers"));
        }
        buffer.extend_from_slice(&temp[..n]);
        if let Some(pos) = buffer.windows(4).position(|w| w == b"\r\n\r\n") {
            header_end = pos + 4;
            break;
        }
        if buffer.len() > 1024 * 1024 {
            return Err(anyhow!("Headers too large"));
        }
    }

    let headers_str = String::from_utf8_lossy(&buffer[..header_end]);
    let content_length = headers_str
        .lines()
        .find(|line| line.to_lowercase().starts_with("content-length:"))
        .and_then(|line| line.split(':').nth(1))
        .and_then(|s| s.trim().parse::<usize>().ok())
        .unwrap_or(0);

    // Read body
    let mut body = Vec::new();
    body.extend_from_slice(&buffer[header_end..]);
    while body.len() < content_length {
        let n = stream.read(&mut temp).await?;
        if n == 0 {
            return Err(anyhow!("Connection closed while reading body"));
        }
        body.extend_from_slice(&temp[..n]);
    }

    // Health endpoint
    if headers_str.starts_with("GET /health") {
        let response_body = b"ok";
        stream.write_all(b"HTTP/1.1 200 OK\r\n").await?;
        stream.write_all(b"Content-Type: text/plain\r\n").await?;
        stream.write_all(format!("Content-Length: {}\r\n", response_body.len()).as_bytes()).await?;
        stream.write_all(b"Connection: close\r\n\r\n").await?;
        stream.write_all(response_body).await?;
        stream.flush().await?;
        return Ok(());
    }

    // Parse JSON envelope
    let request: Value = serde_json::from_slice(&body)?;
    let mut request_body = if let Some(inner) = request.get("body") {
        inner.clone()
    } else {
        request.clone()
    };
    if request_body.get("model").is_none() {
        request_body["model"] = json!(default_model);
    }

    // Forward to DeepSeek
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

    // Write response back using same stream
    stream.write_all(format!("HTTP/1.1 {}\r\n", status).as_bytes()).await?;
    stream.write_all(b"Content-Type: application/json\r\n").await?;
    stream.write_all(format!("Content-Length: {}\r\n", response_bytes.len()).as_bytes()).await?;
    stream.write_all(b"Connection: close\r\n\r\n").await?;
    stream.write_all(&response_bytes).await?;
    stream.flush().await?;
    Ok(())
}


async fn run_tcp(port: u16, api_key: String, model: String) -> Result<()> {
    // Bind to 0.0.0.0 so Docker containers can reach via host.docker.internal
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await?;
    eprintln!("Provider proxy (TCP) listening on {}", addr);

    loop {
        let (mut socket, _) = listener.accept().await?;
        let api_key = api_key.clone();
        let model = model.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_client(&mut socket, &api_key, &model).await {
                eprintln!("Client error: {}", e);
            }
        });
    }
}


async fn run_unix(socket_path: PathBuf, api_key: String, model: String) -> Result<()> {
    if socket_path.exists() {
        std::fs::remove_file(&socket_path)?;
    }
    if let Some(parent) = socket_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let listener = UnixListener::bind(&socket_path)?;
    let metadata = std::fs::metadata(&socket_path)?;
    let mut perms = metadata.permissions();
    perms.set_mode(0o600);
    std::fs::set_permissions(&socket_path, perms)?;
    eprintln!("Provider proxy (Unix socket) listening on {}", socket_path.display());

    loop {
        let (mut socket, _) = listener.accept().await?;
        let api_key = api_key.clone();
        let model = model.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_client(&mut socket, &api_key, &model).await {
                eprintln!("Client error: {}", e);
            }
        });
    }
}


#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    let tcp_mode = args.iter().any(|a| a == "--tcp");
    let port = args
        .iter()
        .position(|a| a == "--port")
        .and_then(|i| args.get(i + 1))
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(DEFAULT_PORT);

    let api_key = env::var("DEEPSEEK_API_KEY").expect("DEEPSEEK_API_KEY not set");
    let model = env::var("CLANKER_MODEL").unwrap_or_else(|_| DEFAULT_MODEL.to_string());

    if tcp_mode {
        run_tcp(port, api_key, model).await?;
    } else {
        let socket_path = expand_tilde(DEFAULT_SOCKET);
        run_unix(socket_path, api_key, model).await?;
    }
    Ok(())
}
