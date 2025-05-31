# Strartup
docker compose down
docker compose up -d

https://openwebui.tail37f875.ts.net

### tailscale commands 
docker exec -it tailscale tailscale status
docker exec -it tailscale tailscale serve status

## Docker Commands

### Entering a container

CMD 
docker exec -it tailscale sh

CMD: when requiring a clean reset, as in, currently in a bad state
docker compose down
rm -rf ./data/tailscale
docker compose up -d --build


