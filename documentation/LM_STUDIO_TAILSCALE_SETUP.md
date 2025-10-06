# LM Studio Tailscale Integration Guide

This guide explains how to expose your LM Studio server through your Tailscale network, similar to how Ollama is exposed.

## Overview

LM Studio will be accessible via your Tailnet at:
```
https://your-hostname.tail[...].ts.net/lmstudio
```

## Prerequisites

1. **LM Studio installed and running** on your host machine
2. **Server mode enabled** in LM Studio
3. **Models loaded** and ready to serve
4. **Tailscale working** in your AI stack

## Setup Steps

### 1. Configure LM Studio Server

1. Open LM Studio on your host machine
2. Go to the **Local Server** tab
3. Load a model of your choice
4. Start the server (default port is 1234)
5. Ensure "Enable CORS" is checked if you plan to use it from web applications
6. Note the port number (usually 1234)

### 2. Configure Environment Variables

Create a `.env` file from the example if you haven't already:
```bash
cp .env.example .env
```

Edit your `.env` file and ensure these LM Studio settings are configured:
```bash
# LM Studio Configuration
LMSTUDIO_PORT=1234          # Change if LM Studio uses a different port
LMSTUDIO_ENABLED=true       # Set to false to disable LM Studio integration
```

### 3. Restart Your Tailscale Container

After updating the environment variables, restart the Tailscale container to pick up the new configuration:

```powershell
# Quick restart method
docker compose restart tailscale

# Or if you need a full rebuild
docker compose down tailscale
docker compose up -d tailscale
```

### 4. Verify Setup

Check that everything is working:

```powershell
# Check Tailscale serve status
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status

# Check if LM Studio is reachable from container
docker compose exec tailscale wget -q -T 5 -O /dev/null http://host.docker.internal:1234/v1/models && echo "LM Studio accessible" || echo "LM Studio not accessible"

# View container logs for setup process
docker compose logs tailscale | grep -i "lm studio"
```

## Usage

### Direct API Access

Once configured, you can access LM Studio's API through your Tailnet:

```bash
# List available models
curl -k https://your-hostname.tail[...].ts.net/lmstudio/v1/models

# Chat completion
curl -k -X POST https://your-hostname.tail[...].ts.net/lmstudio/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-name",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### OpenAI-Compatible Client Usage

You can use any OpenAI-compatible client by pointing it to:
- **Base URL**: `https://your-hostname.tail[...].ts.net/lmstudio/v1`
- **API Key**: Not required for LM Studio (use any dummy value)

Example with Python OpenAI client:
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://your-hostname.tail[...].ts.net/lmstudio/v1",
    api_key="dummy-key"  # LM Studio doesn't require a real API key
)

response = client.chat.completions.create(
    model="your-model-name",
    messages=[{"role": "user", "content": "Hello from Tailscale!"}]
)
print(response.choices[0].message.content)
```

## Troubleshooting

### LM Studio Not Accessible

1. **Check if LM Studio server is running**:
   ```bash
   curl http://localhost:1234/v1/models
   ```

2. **Verify the port**:
   - Check LM Studio interface for the correct port
   - Update `LMSTUDIO_PORT` in your `.env` file if different

3. **Check firewall settings**:
   - Ensure Windows Firewall allows LM Studio
   - Port 1234 should be accessible from Docker containers

### Tailscale Configuration Issues

1. **Check serve status**:
   ```powershell
   docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status
   ```

2. **View setup logs**:
   ```powershell
   docker compose logs tailscale | grep -i "lm studio"
   ```

3. **Manual serve configuration** (if automatic setup failed):
   ```powershell
   docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve --https=443 --set-path=/lmstudio --bg http://host.docker.internal:1234
   ```

### Network Connectivity Issues

1. **Test host.docker.internal resolution**:
   ```powershell
   docker compose exec tailscale nslookup host.docker.internal
   ```

2. **Test direct connectivity**:
   ```powershell
   docker compose exec tailscale wget -q -T 5 -O /dev/null http://host.docker.internal:1234/v1/models
   ```

### Disable LM Studio Integration

If you want to disable LM Studio integration temporarily:

```bash
# In your .env file
LMSTUDIO_ENABLED=false
```

Then restart the Tailscale container:
```powershell
docker compose restart tailscale
```

## Security Considerations

1. **Network Access**: LM Studio will be accessible to anyone on your Tailnet
2. **No Authentication**: LM Studio doesn't require API keys by default
3. **HTTPS**: All traffic is encrypted via Tailscale's HTTPS serve feature
4. **Firewall**: Consider restricting LM Studio to only accept connections from Docker containers

## Integration with OpenWebUI

You can add LM Studio as a model provider in OpenWebUI:

1. Go to OpenWebUI Settings → Models
2. Add a new connection:
   - **API Base URL**: `https://your-hostname.tail[...].ts.net/lmstudio/v1`
   - **API Key**: Any dummy value (e.g., "lm-studio")
3. The models from LM Studio should appear in your model list

## Performance Notes

- **Direct vs Proxied**: Access through Tailscale adds minimal latency
- **Model Loading**: Ensure models are pre-loaded in LM Studio for best performance  
- **Concurrent Requests**: LM Studio's performance depends on your hardware and model size
- **Network**: Tailscale uses WireGuard which is very efficient for API traffic

## Advanced Configuration

### Custom Port
If LM Studio runs on a different port:
```bash
# In .env file
LMSTUDIO_PORT=8080  # or whatever port LM Studio uses
```

### Multiple LM Studio Instances
Currently, the configuration supports one LM Studio instance. For multiple instances, you would need to:
1. Run them on different ports
2. Modify the entrypoint.sh to add additional serve configurations
3. Use different path prefixes (e.g., `/lmstudio1`, `/lmstudio2`)

### CORS Configuration
If accessing from web applications, ensure LM Studio has CORS enabled:
1. Open LM Studio Settings
2. Enable "CORS" in the server settings
3. Add your Tailscale hostname to allowed origins if needed

---

This integration provides seamless access to your LM Studio models through your secure Tailscale network, complementing your existing Ollama setup.