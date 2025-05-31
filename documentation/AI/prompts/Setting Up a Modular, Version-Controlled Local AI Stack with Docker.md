

# 🚀 Beginner-Friendly Tutorial: Setting Up a Modular, Version-Controlled Local AI Stack with Docker
---

## 1. Introduction

### What This Tutorial Sets Up
This tutorial guides you through creating a **modular, version-controlled, and resilient local AI stack** using Docker. The stack includes:
- **Open WebUI**: A web interface for interacting with AI models.
- **Ollama**: A tool for running and managing large language models.
- **Tailscale**: A secure, private network for exposing your AI stack.

### Why This Stack is Useful
- **Modular**: Each service (Open WebUI, Ollama, Tailscale) runs in its own container, making it easy to scale or replace components.
- **Version-Controlled**: All configurations are stored in Git, allowing you to track changes and revert to previous states.
- **Resilient**: Docker ensures services restart automatically, and isolated volumes protect your data.

---

## 2. Install Prerequisites

### 🐳 Docker Desktop
Download and install Docker Desktop from [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop).  
⚠️ Make sure Docker Desktop is running before continuing.

### 🧮 Git
Install Git from [https://git-scm.com](https://git-scm.com).  
Run `git --version` to confirm installation.

### 🌐 Tailscale
Install Tailscale from [https://tailscale.com/download](https://tailscale.com/download).  
Run `tailscale --version` to confirm installation.

---

## 3. Create Project Structure

### 📁 Create a Parent Folder
```bash
mkdir ai-stack
cd ai-stack
```

### 📁 Create Required Directories and Files
```bash
mkdir config scripts
touch .env docker-compose.yml .gitignore
```

**Directory Structure**
```
ai-stack/
├── config/
├── scripts/
├── .env
├── docker-compose.yml
└── .gitignore
```

---

## 4. Initialize Git Repository

### 🧾 Initialize Git
```bash
git init
git add .
git commit -m "Initial commit"
```

---

## 5. Define Environment Variables

### 📜 Sample `.env` File
Create and edit `.env` with these lines:
```env
OLLAMA_HOST=ollama
OPEN_WEBUI_HOST=openwebui
TAILSCALE_AUTH_KEY=your-tailscale-auth-key
```

💡 Replace `your-tailscale-auth-key` with your actual Tailscale authentication key.

---

## 6. Write `docker-compose.yml`

### 📁 Define Services
```yaml
version: '3.8'

services:
  openwebui:
    image: openwebui/openwebui:latest
    container_name: openwebui
    ports:
      - "3000:3000"
    volumes:
      - openwebui_sessions:/app/data
      - ./config:/app/config
    environment:
      - OLLAMA_HOST=${OLLAMA_HOST}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000"]
      interval: 5s
      timeout: 3s
      retries: 3

  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_models:/models
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434"]
      interval: 5s
      timeout: 3s
      retries: 3

  tailscale:
    image: tailscale/tailscale:latest
    container_name: tailscale
    volumes:
      - /run/tailscale:/run/tailscale
    environment:
      - TAILSCALE_AUTH_KEY=${TAILSCALE_AUTH_KEY}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:853"]
      interval: 5s
      timeout: 3s
      retries: 3

volumes:
  openwebui_sessions:
  ollama_models:
  ai_logs:
```

---

## 7. Set Up Docker Volumes

### 🧱 Define Volumes
- **`openwebui_sessions`**: Stores user sessions for Open WebUI.
- **`ollama_models`**: Stores downloaded models for Ollama.
- **`ai_logs`**: Stores logs for debugging.

💡 These volumes are isolated, so changes to one won’t affect others.

---

## 8. Bind-Mount Static Config

### 📁 What Goes in `config/`
Place static configuration files here, like:
- `config/openwebui.conf`
- `config/ollama.conf`

### 🧩 Mount into Open WebUI
In `docker-compose.yml`, the `./config:/app/config` line mounts this directory into Open WebUI.

---

## 9. Secure the Stack with Tailscale

### 🔐 Join Your Tailnet
Run this command to join your Tailscale network:
```bash
tailscale up --auth-key=${TAILSCALE_AUTH_KEY}
```

### 🚪 Expose the Stack Securely
Update `docker-compose.yml` to expose services via Tailscale:
```yaml
networks:
  tailscale:
    external:
      name: tailscale
```

💡 Ensure your firewall allows Tailscale traffic (port 853).

---

## 10. Start the Stack

### 🚀 Run the Stack
```bash
docker compose up -d
```

### ✅ Confirm Services Are Healthy
```bash
docker compose ps
```

![Description](screenshot-path)  
**Screenshot: Open WebUI interface after successful startup**

---

## 11. Backup Procedures

### 📤 Create a Backup Script
Create `scripts/backup.sh`:
```bash
#!/bin/bash
tar -czf backups/ai-stack-$(date +%Y%m%d).tar.gz -C .. .
```

### 📅 Schedule Backups (Optional)
Add this to your crontab:
```bash
0 2 * * * /path/to/scripts/backup.sh
```

---

## 12. Restore Procedures

### 🔄 Restore from Backup
Extract the backup:
```bash
tar -xzf backups/ai-stack-YYYYMMDD.tar.gz -C ..
```

### 🧼 Reinitialize Git
```bash
git init
git add .
git commit -m "Restored from backup"
```

---

## 13. Git Workflow for Config Changes

### 🔄 Create a Branch
```bash
git checkout -b feature/new-config
```

### ✏️ Make Changes
Edit files in `config/` or `.env`.

### 🧱 Merge Back to `main`
```bash
git checkout main
git merge feature/new-config
```

---

## 14. Experimental Overrides

### 🧪 Create `docker-compose.override.yml`
Add this file to override services:
```yaml
version: '3.8'

services:
  coqui-tts:
    image: coqui/tts:latest
    container_name: coqui-tts
    ports:
      - "5000:5000"
    volumes:
      - ./config:/app/config
    restart: unless-stopped
```

### 🔄 Apply Overrides
Run:
```bash
docker compose up -d
```

---

## 🎉 Final Notes

- **Backup regularly** to avoid data loss.
- **Test overrides** in a separate branch to avoid breaking the main stack.
- **Explore Tailscale’s advanced features** like mesh networking for multi-device setups.

---

## 📚 Next Steps
- Add monitoring with Prometheus and Grafana.
- Integrate with a cloud storage backend for models.
- Explore advanced Docker networking for multi-node setups.

---

**Happy Hacking!** 🧠💻