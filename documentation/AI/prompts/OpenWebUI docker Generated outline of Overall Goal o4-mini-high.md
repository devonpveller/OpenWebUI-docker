The aim of this setup is to create a **modular, version-controlled, and resilient** local AI stack—combining Open WebUI, Ollama, and secure Tailscale networking—such that:

* **Configuration is fully Git-backed**: all hand-edited YAML and env files live in a lightweight repo for auditability and rollback.
* **Data is isolated by purpose**: separate Docker volumes for models, session data, and logs so you can snapshot or restore only what you need.
* **Services remain decoupled**: each component runs in its own container, minimizing blast radius when experimenting or upgrading.
* **Backups and restores are predictable**: clearly defined scripts and workflows let you tear down and rebuild any part of the stack without losing critical data.
* **Experiments stay reversible**: override files and Git branches let you spin up proofs-of-concept (e.g. Coqui-TTS) without affecting your “golden” configuration.

* **Initialize Git Repository for Configuration and Manifests**
* **Create Base Directory Layout** (config folder, compose file location, backup scripts)
* **Establish Non-Secret Environment Definitions** (.env file for Compose)
* **Define Service Containers in docker-compose.yml** (Open WebUI, Ollama, Tailscale)
* **Configure Docker Networks for Service Segmentation**
* **Bind-Mount Static Configuration Directory into Open WebUI**
* **Declare Granular Named Volumes** (sessions, models, logs)
* **Add Healthchecks and Restart Policies for Each Service**
* **Configure Docker Secrets for Sensitive Credentials**
* **Commit Initial Directory Layout and Compose Files to Git**
* **Establish Git-Based Configuration Change Workflow** (branching, commits, merges)
* **Outline Volume Backup Procedures** (snapshot scripts, scheduling)
* **Outline Volume Restore Procedures** (rehydration workflows)
* **Outline Experimental Overrides via Compose Override Files**
