use anyhow::{anyhow, Result};
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Client, Method, Request, Response, Server, StatusCode};
use std::convert::Infallible;
use std::env;
use std::path::PathBuf;
use tokio::net::{TcpListener, UnixListener};

const API_KEY_ENV: &str = "DEEPSEEK_API_KEY";
const BASE_URL: &str = "https://api.deepseek.com/v1/chat/completions";
const DEFAULT_PORT: u16 = 11434;
const DEFAULT_SOCKET: &str = "~/.cache/clanker/provider.sock";

fn expand_tilde(path: &str) -> PathBuf {
    if let Some(stripped) = path.strip_prefix("~/") {
        let home = env::var("HOME").expect("HOME not set");
        PathBuf::from(home).join(stripped)
    } else {
        PathBuf::from(path)
    }
}

async fn handle_request(req: Request<Body>) -> Result<Response<Body>, Infallible> {
    // Health check
    if req.method() == Method::GET && req.uri().path() == "/health" {
        return Ok(Response::new(Body::from("ok")));
    }

    // Only accept POST
    if req.method() != Method::POST {
        return Ok(Response::builder()
            .status(StatusCode::METHOD_NOT_ALLOWED)
            .body(Body::from("Only POST allowed"))
            .unwrap());
    }

    // Read the full request body
    let body_bytes = match hyper::body::to_bytes(req.into_body()).await {
        Ok(b) => b,
        Err(e) => {
            return Ok(Response::builder()
                .status(StatusCode::BAD_REQUEST)
                .body(Body::from(format!("Failed to read body: {}", e)))
                .unwrap());
        }
    };

    // Parse envelope: the agent-loop sends {"body": {...}} or just the request body
    let envelope: serde_json::Value = match serde_json::from_slice(&body_bytes) {
        Ok(v) => v,
        Err(e) => {
            return Ok(Response::builder()
                .status(StatusCode::BAD_REQUEST)
                .body(Body::from(format!("Invalid JSON: {}", e)))
                .unwrap());
        }
    };

    let inner_body = envelope
        .get("body")
        .cloned()
        .unwrap_or_else(|| envelope.clone());

    // Ensure model is set
    let mut inner_body = inner_body;
    if inner_body.get("model").is_none() {
        let default_model = env::var("CLANKER_MODEL").unwrap_or_else(|_| "deepseek-chat".to_string());
        inner_body["model"] = serde_json::json!(default_model);
    }

    // Get API key
    let api_key = match env::var(API_KEY_ENV) {
        Ok(k) => k,
        Err(_) => {
            return Ok(Response::builder()
                .status(StatusCode::INTERNAL_SERVER_ERROR)
                .body(Body::from("DEEPSEEK_API_KEY not set"))
                .unwrap());
        }
    };

    // Forward to DeepSeek using reqwest
    let client = reqwest::Client::new();
    let upstream_resp = match client
        .post(BASE_URL)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&inner_body)
        .send()
        .await
    {
        Ok(resp) => resp,
        Err(e) => {
            return Ok(Response::builder()
                .status(StatusCode::BAD_GATEWAY)
                .body(Body::from(format!("Upstream error: {}", e)))
                .unwrap());
        }
    };

    let status = upstream_resp.status().as_u16();
    let headers = upstream_resp.headers().clone();

    // Build the response to the agent-loop. We stream the upstream body.
    let mut response = Response::builder()
        .status(status)
        .body(Body::wrap_stream(upstream_resp.bytes_stream()))
        .unwrap();

    // Copy relevant headers (content-type, etc.)
    for (key, value) in headers.iter() {
        if key == "content-type" || key == "content-length" {
            response.headers_mut().insert(key, value.clone());
        }
    }

    Ok(response)
}

async fn run_tcp(port: u16) -> Result<()> {
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await?;
    eprintln!("Provider proxy (TCP) listening on {}", addr);


    loop {
        let (stream, _) = listener.accept().await?;
        tokio::spawn(async move {
            let service = service_fn(handle_request);
            let http = hyper::server::conn::Http::new();
            if let Err(e) = http.serve_connection(stream, service).await {
                eprintln!("Connection error: {}", e);
            }
        });
    }
}


async fn run_unix(socket_path: PathBuf) -> Result<()> {
    if socket_path.exists() {
        std::fs::remove_file(&socket_path)?;
    }
    if let Some(parent) = socket_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let listener = UnixListener::bind(&socket_path)?;
    // Set permissions to 0o600
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let metadata = std::fs::metadata(&socket_path)?;
        let mut perms = metadata.permissions();
        perms.set_mode(0o600);
        std::fs::set_permissions(&socket_path, perms)?;
    }
    eprintln!("Provider proxy (Unix socket) listening on {}", socket_path.display());

    loop {
        let (stream, _) = listener.accept().await?;
        tokio::spawn(async move {

            let service = service_fn(handle_request);

            //let service = make_service_fn(|_| async {
            //    Ok::<_, Infallible>(service_fn(handle_request))
            //});

            let http = hyper::server::conn::Http::new();
            if let Err(e) = http
                .serve_connection(stream, service)
                .await
            {
                eprintln!("Connection error: {}", e);
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

    if tcp_mode {
        run_tcp(port).await?;
    } else {
        let socket_path = expand_tilde(DEFAULT_SOCKET);
        run_unix(socket_path).await?;
    }
    Ok(())
}
