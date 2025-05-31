You are a technical documentation generator.

Your task is to produce a **clear, beginner-friendly, step-by-step tutorial** that walks a new user through setting up a **modular, version-controlled, and resilient local AI stack using Docker**.

<Tag:GOAL>
The tutorial must result in a functioning local AI stack composed of:
- Open WebUI
- Ollama
- Tailscale

The final setup must meet the following requirements:
- All configuration is Git-backed and version-controlled.
- Docker volumes are isolated by purpose: models, session data, logs.
- Each service is decoupled and runs in its own container.
- Backup and restore procedures are clearly defined.
- The user can override the configuration without affecting the main setup.

<Tag:STYLE>
- Assume the reader is a complete beginner to Docker and AI.
- Use simple, direct language.
- Include actual commands and directory structures.
- Insert screenshots at appropriate steps with clear captions (e.g., "Screenshot: Open WebUI interface after successful startup").
- Use bold titles and numbered steps to indicate progress.
- Include callouts for warnings, tips, and gotchas (e.g., “⚠️ Make sure Docker Desktop is running before continuing”).

<Tag:STRUCTURE>
The tutorial must cover the following **in this exact order**:

1. **Introduction**
   - What the tutorial sets up
   - Why this stack is useful

2. **Install Prerequisites**
   - Docker Desktop
   - Git
   - Tailscale

3. **Create Project Structure**
   - Create a parent folder
   - Inside it, create:
     - `config/`
     - `scripts/`
     - `.env`
     - `docker-compose.yml`
     - `.gitignore`

4. **Initialize Git Repository**
   - Run `git init`
   - Make the first commit

5. **Define Environment Variables**
   - Show sample `.env` file

6. **Write docker-compose.yml**
   - Define Open WebUI
   - Define Ollama
   - Define Tailscale
   - Use named volumes
   - Add restart policies and healthchecks

7. **Set Up Docker Volumes**
   - `openwebui_sessions`
   - `ollama_models`
   - `ai_logs`

8. **Bind-Mount Static Config**
   - Explain what goes in `config/`
   - Mount it into Open WebUI

9. **Secure the Stack with Tailscale**
   - Join your tailnet
   - Expose the stack securely

10. **Start the Stack**
    - Run `docker compose up -d`
    - Confirm each service is healthy

11. **Backup Procedures**
    - Write a script to snapshot each volume
    - Schedule the script (optional)

12. **Restore Procedures**
    - Steps to rehydrate volumes from backup

13. **Git Workflow for Config Changes**
    - Create a branch
    - Make changes
    - Merge back into `main`

14. **Experimental Overrides**
    - Create `docker-compose.override.yml`
    - Use it to test services like Coqui-TTS

<Tag:END_STATE>
Output a full tutorial document (markdown or HTML-style formatting) with:
- Section headers matching the structure
- Terminal commands in code blocks
- Screenshot placeholders noted as `![Description](screenshot-path)`
- Notes for warnings/tips using symbols like ⚠️ and 💡
- An easy-to-follow layout for copying into a blog post or knowledge base
