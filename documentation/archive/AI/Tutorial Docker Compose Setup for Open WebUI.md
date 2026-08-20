## **1. Introduction**

Welcome to this step-by-step guide! You’ll learn how to build a **modular, version-controlled, and resilient** local AI stack using Docker, consisting of:

* **Open WebUI** (for AI interfaces)
* **Ollama** (local model server)
* **Tailscale** (secure networking)

**Why this stack is useful:**

* **Decoupled services:** each runs in its own container for easy upgrades
* **Git-backed configs:** track changes, collaborate, and roll back
* **Isolated volumes:** keep models, session data, and logs separate
* **Easy backup & restore:** automated scripts let you snapshot and recover
* **Experimental overrides:** test new services without touching main setup

---

## **2. Install Prerequisites**

1. **Docker Desktop**

   * Download & install from [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
   * ⚠️ **Make sure Docker Desktop is running** before proceeding.

2. **Git**

   * Download & install from [https://git-scm.com/](https://git-scm.com/)
   * Verify with:

     ```bash
     git --version
     ```

3. **Tailscale**

   * **Windows/macOS:** download from [https://tailscale.com/download](https://tailscale.com/download)
   * **Linux (Debian/Ubuntu):**

     ```bash
     curl -fsSL https://tailscale.com/install.sh | sh
     sudo tailscale up
     ```
   * Confirm with:

     ```bash
     tailscale status
     ```

---

## **3. Create Project Structure**

1. Open a terminal and choose a parent folder (e.g., your home directory).

2. Create the project layout:

   ```bash
   mkdir -p ai-stack/{config,scripts,backups}
   cd ai-stack
   touch .env docker-compose.yml .gitignore
   ```

3. You should now see:

   ```
   ai-stack/
   ├── backups/
   ├── config/
   ├── scripts/
   ├── .env
   ├── .gitignore
   └── docker-compose.yml
   ```

---

## **4. Initialize Git Repository**

1. In the `ai-stack/` folder, run:

   ```bash
   git init
   git add .
   git commit -m "chore: initial project structure"
   ```
2. 💡 **Tip:** Add a remote (`git remote add origin <url>`) if you want to push to GitHub or GitLab.

---

## **5. Define Environment Variables**

Create a sample `.env` file:

```dotenv
# .env
OPENWEBUI_PORT=3000
OLLAMA_PORT=11434
TAILSCALE_AUTHKEY=tskey-your-authkey-here
```

* **OPENWEBUI\_PORT**: port for Open WebUI
* **OLLAMA\_PORT**: port for Ollama
* **TAILSCALE\_AUTHKEY**: your tailnet auth key

---

## **6. Write docker-compose.yml**

Open `docker-compose.yml` and paste:

```yaml
version: "3.8"
services:
  openwebui:
    image: ghcr.io/open-webui/open-webui:latest
    container_name: openwebui
    ports:
      - "${OPENWEBUI_PORT}:3000"
    volumes:
      - openwebui_sessions:/data/sessions
      - ai_logs:/app/logs
      - ./config/openwebui_config.yaml:/app/config/config.yaml:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "${OLLAMA_PORT}:11434"
    volumes:
      - ollama_models:/var/lib/ollama/models
      - ai_logs:/var/log/ollama
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "ollama", "health"]
      interval: 30s
      timeout: 10s
      retries: 3

  tailscale:
    image: tailscale/tailscale:stable
    container_name: tailscale
    network_mode: "host"
    user: root
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    volumes:
      - ./config/tailscale:/var/lib/tailscale
    environment:
      - TS_AUTHKEY=${TAILSCALE_AUTHKEY}
    entrypoint: ["tailscale", "up", "--authkey=${TAILSCALE_AUTHKEY}", "--hostname=ai-stack"]
    restart: unless-stopped

volumes:
  openwebui_sessions:
  ollama_models:
  ai_logs:
```

---

## **7. Set Up Docker Volumes**

Run the following to pre-create named volumes:

```bash
docker volume create openwebui_sessions
docker volume create ollama_models
docker volume create ai_logs
```

💡 **Tip:** You can skip this—`docker compose up` will auto-create them.

---

## **8. Bind-Mount Static Config**

* **What goes in `config/`:**

  * `openwebui_config.yaml` (Open WebUI settings)
  * `tailscale` folder (state files)

* **Example `config/openwebui_config.yaml`:**

  ```yaml
  # Example settings
  theme: dark
  enable_experimental: false
  ```

* This file is read-only inside the container (`:ro`).

---

## **9. Secure the Stack with Tailscale**

1. **Join your tailnet**
   Tailscale service will auto-start and join using your auth key.

2. **Confirm in Tailscale Admin Console**
   ![Screenshot: Tailscale Tailnet Devices](path/to/tailscale_screenshot.png)

3. **Access services via `*.ts.net`**

   * `https://ai-stack.tail37f875.ts.net:3000` → Open WebUI
   * `https://ai-stack.tail37f875.ts.net:11434` → Ollama API

---

## **10. Start the Stack**

1. Run in your project root:

   ```bash
   docker compose up -d
   ```

2. Check status:

   ```bash
   docker compose ps
   ```

   ![Screenshot: docker compose ps showing healthy containers](path/to/docker_ps_screenshot.png)

3. Visit **Open WebUI** in your browser:

   ```
   http://localhost:3000
   ```

   ![Screenshot: Open WebUI interface after successful startup](path/to/openwebui_screenshot.png)

---

## **11. Backup Procedures**

1. **Create `scripts/backup.sh`:**

   ```bash
   #!/usr/bin/env bash
   TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
   mkdir -p backups/$TIMESTAMP

   for vol in openwebui_sessions ollama_models ai_logs; do
     docker run --rm \
       -v ${vol}:/volume \
       -v $(pwd)/backups/$TIMESTAMP:/backup \
       alpine \
       sh -c "cd /volume && tar czf /backup/${vol}.tar.gz ."
   done

   echo "Backups saved to backups/$TIMESTAMP/"
   ```
2. Make it executable:

   ```bash
   chmod +x scripts/backup.sh
   ```
3. **(Optional) Schedule daily backups** with cron:

   ```cron
   # Run at 2 AM every day
   0 2 * * * /full/path/to/ai-stack/scripts/backup.sh
   ```

---

## **12. Restore Procedures**

1. **Stop containers:**

   ```bash
   docker compose down
   ```
2. **Extract backups** (replace `TIMESTAMP`):

   ```bash
   for vol in openwebui_sessions ollama_models ai_logs; do
     tar xzf backups/TIMESTAMP/${vol}.tar.gz \
       -C /var/lib/docker/volumes/${vol}/_data
   done
   ```
3. **Restart:**

   ```bash
   docker compose up -d
   ```

---

## **13. Git Workflow for Config Changes**

1. **Create a branch:**

   ```bash
   git checkout -b feature/update-openwebui-config
   ```
2. **Edit files** in `config/` or `docker-compose.yml`.
3. **Commit & push:**

   ```bash
   git add config/openwebui_config.yaml
   git commit -m "chore: adjust Open WebUI theme"
   git push -u origin feature/update-openwebui-config
   ```
4. **Merge into `main`:**

   ```bash
   git checkout main
   git merge feature/update-openwebui-config
   git push
   ```

---

## **14. Experimental Overrides**

1. **Create `docker-compose.override.yml`:**

   ```yaml
   version: "3.8"
   services:
     openwebui:
       environment:
         - ENABLE_COQUI_TTS=true
       volumes:
         - ./config/coqui_tts_config.json:/app/config/coqui_tts.json:ro
   ```
2. **Start with override:**

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.override.yml up -d openwebui
   ```
3. Test Coqui-TTS or other services without touching `main` setup.
4. To revert, rename or remove `docker-compose.override.yml`.

---

🎉 **Congratulations!** You now have a fully version-controlled, modular AI stack running locally with Open WebUI, Ollama, and Tailscale—ready for backups, restores, and safe experimentation.
