# Startup
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


# Todo
Connect to automatic1111
https://chatgpt.com/share/683af1ee-0d3c-8002-8b49-c70dafe578f5
Generally, connecting to Automatic1111 while openwebui is in a docker container requires an additional environment variable.

When adding the nlp service, connecting from another device shows an error to fail to connect, while the host machine is fine.