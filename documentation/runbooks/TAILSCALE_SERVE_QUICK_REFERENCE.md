# Tailscale Serve Quick Reference

## 🚀 Quick Start

### Serve LM Studio
```
start serving lmstudio on port 5506
```

### Check What's Running
```
show tailscale serve status
```

### Stop a Service
```
stop serving lmstudio
```

## 💬 Natural Language Commands

| What You Want | What To Say |
|---------------|-------------|
| Expose LM Studio | `start serving lmstudio on port 5506` |
| Expose custom service | `expose myapp at /myapp on port 8080` |
| Stop LM Studio | `stop serving lmstudio` |
| Check status | `show tailscale status` |
| Health check | `check health of lmstudio` |

## 🎯 Common Use Cases

### 1. Share LM Studio with Your Tailnet
```
You: start serving lmstudio on port 5506

AI: ✅ Successfully started serving at /lmstudio
    🌐 Access URL: https://your-machine.tailnet.dev/lmstudio
```

### 2. Expose a Local Web App
```
You: expose myapp at /myapp on port 3000

AI: ✅ Successfully started serving at /myapp
    🌐 Access URL: https://your-machine.tailnet.dev/myapp
```

### 3. Check Running Services
```
You: what's being served on tailscale

AI: Currently Served Paths:
    - /lmstudio → http://127.0.0.1:5506
    - /myapp → http://127.0.0.1:3000
```

## ⚡ Quick Fixes

| Problem | Solution |
|---------|----------|
| "Tailscale not ready" | Run: `fix namespace` |
| "Target unreachable" | Check if LM Studio is running |
| "Path conflict" | Stop existing service first |
| "Authentication required" | Check Tailscale auth key |

## 🔧 Advanced Usage

### Serve with Custom Health Path
```
start serving api at /api on port 8000 with health path /health
```

### Check Service Health
```
check if lmstudio is healthy
```

### Multiple Services
```
start serving lmstudio on port 5506
start serving jupyter at /notebook on port 8888
start serving api at /api on port 3000
```

## 📋 Error Messages Explained

| Error | Meaning | Fix |
|-------|---------|-----|
| **TAILSCALE_NOT_READY** | Tailscale isn't running | `fix namespace` |
| **AUTH_REQUIRED** | Need authentication | Update auth key in `.env` |
| **TARGET_UNREACHABLE** | Service isn't responding | Start the service |
| **SERVE_CONFLICT** | Path already in use | `stop serving <path>` first |

## 🌐 Accessing Your Services

Once served, access via:
- **URL Format**: `https://your-machine.tailnet.dev/<path>`
- **Example**: `https://owui-node.tailnet.dev/lmstudio`
- **Security**: Only accessible to your Tailscale network

## 💡 Tips & Tricks

1. **Default Ports**: LM Studio = 5506, Jupyter = 8888, API = 3000
2. **Path Names**: Use lowercase, no spaces (e.g., `/my-app`)
3. **Health Checks**: System monitors service health automatically
4. **Multiple Paths**: You can serve many services simultaneously
5. **Clean URLs**: Choose memorable path names like `/studio` or `/api`

## 🔐 Security Notes

- ✅ Only accessible via your Tailscale network
- ✅ No public internet exposure
- ✅ End-to-end encrypted
- ✅ Per-device access control via Tailscale ACLs

## 📞 Getting Help

```
show tailscale commands
help with tailscale serve
what can I expose with tailscale
```

## 🎓 Learn More

- Ask: "how does tailscale serve work?"
- Ask: "show me tailscale serve examples"
- Ask: "explain tailscale networking"

---

**Pro Tip**: Save commonly used URLs in your browser bookmarks for quick access!
