# Deploying Continuum — free, public, permanent

One free Oracle Cloud VM runs everything: the API, Qdrant, Postgres, Ollama (the
LLM and embedder), Whisper and Piper — and Caddy, which serves the UI over HTTPS
and is the only thing the internet can reach. Your memories never leave the VM.

```
internet ──443──▶ web (Caddy: UI + /api proxy, HTTPS)
                     │  compose network only
                     ├──▶ api ──▶ qdrant · postgres · ollama
```

Files: `continuum-be/production.yml` (the stack), `continuum-fe/Dockerfile` +
`Caddyfile` (the front door), `continuum-be/.envs/.production/*.example`
(settings templates), `continuum-be/scripts/backup-production.sh`.

Time: about an hour, most of it waiting for downloads.

---

## 1. Create the free VM (Oracle Cloud "Always Free")

1. Sign up at <https://www.oracle.com/cloud/free/>. A card is asked for to verify
   you; Always Free resources are not charged. Pick a **home region** close to
   you — Always Free capacity lives there and it cannot be changed later.
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04** (the aarch64 build is chosen with the shape)
   - Shape: **Ampere → VM.Standard.A1.Flex**, **4 OCPUs, 24 GB memory**
     (the whole Always Free allowance)
   - Boot volume: **100 GB** or more (Always Free includes 200 GB in total)
   - Networking: keep "assign a public IPv4 address"
   - SSH keys: upload your public key (`~/.ssh/id_ed25519.pub`; create one with
     `ssh-keygen -t ed25519` if you have none)
   - "Out of capacity"? Try another availability domain, or retry later — Ampere
     capacity frees up through the day.
3. **Open ports 80 and 443**: instance → *Subnet* → *Security Lists* → default
   list → *Add Ingress Rules*: source `0.0.0.0/0`, TCP, destination port `80`;
   again for `443`. (Leave SSH, port 22, as it is. Open nothing else.)
4. Note the instance's **public IP**.

> Oracle can reclaim Always Free instances that sit idle for a long time. If
> that is a concern, upgrading the account to Pay-As-You-Go keeps Always Free
> resources free while removing the idle reclamation — check Oracle's current
> terms before relying on either.

## 2. A free domain (DuckDNS)

HTTPS needs a name, not an IP.

1. Sign in at <https://www.duckdns.org> and create a subdomain, e.g.
   `my-continuum` → `my-continuum.duckdns.org`.
2. Set its IP to the VM's public IP and save.
3. Check from your computer: `ping my-continuum.duckdns.org` shows that IP.

(Already own a domain? Point an A record at the VM instead.)

## 3. Prepare the VM

```bash
ssh ubuntu@<PUBLIC_IP>

# Docker + Compose v2
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit                      # log out and back in for the group to apply
ssh ubuntu@<PUBLIC_IP>
docker compose version    # must print v2.x

# Oracle's Ubuntu image blocks everything but SSH in its own firewall too
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p udp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Get the code

```bash
git clone https://github.com/<you>/<repo>.git Continuum
cd Continuum/continuum-be
```

A private repository: create a fine-grained GitHub token with read access to it
and clone with `https://<token>@github.com/<you>/<repo>.git`, or add the VM's
SSH key as a read-only deploy key.

## 5. Settings and secrets

```bash
cd ~/Continuum/continuum-be/.envs/.production
cp .api.example .api
cp .postgres.example .postgres
cp .web.example .web

# A strong database password, written into both files that need it
DB=$(openssl rand -hex 24)
sed -i "s/<CHANGE_ME_DB_PASSWORD>/$DB/" .api .postgres

nano .api     # ADMIN_EMAIL, ADMIN_PASSWORD (12+ characters)
nano .web     # DOMAIN=my-continuum.duckdns.org, ACME_EMAIL=you@example.com

grep -n CHANGE_ME .api .postgres .web   # must print nothing
chmod 600 .api .postgres .web
```

What these settings do, and why they differ from local development:

| Setting | Why |
| --- | --- |
| `AUTH_ALLOW_WEB_SETUP=false` + `ADMIN_EMAIL`/`ADMIN_PASSWORD` | On a public address, whoever opened the page first would become the admin. The admin is created from these at startup instead. |
| `TRUST_PROXY_HEADERS=true` | Caddy is the only way in and sets `X-Real-IP`, so the sign-in lockout counts visitors, not the proxy. |
| `ENVIRONMENT=production` | Memory content is redacted from the logs. |
| `CORS_ORIGINS=[]` | The UI and API share one origin; no cross-origin caller exists. |

## 6. Start it

```bash
cd ~/Continuum/continuum-be
docker compose -f production.yml up -d --build
docker compose -f production.yml logs -f models api web
```

The first start builds the images and downloads about 6 GB of models (Qwen 7B
and bge-m3), then Whisper and the voice: allow 15–40 minutes. It is ready when
the API logs `continuum.started` and `web` reports a certificate obtained for
your domain.

Open **https://my-continuum.duckdns.org** and sign in with `ADMIN_EMAIL`. Add
other people from **Settings → Users**.

## 7. Afterwards

**Update to a new version**

```bash
cd ~/Continuum && git pull
cd continuum-be && docker compose -f production.yml up -d --build
```

Migrations run on startup; a new embedding model re-embeds on startup. Nothing
else to do.

**Backups** — every night at 03:00, keeping the last 14:

```bash
crontab -e
# add:
0 3 * * * cd /home/ubuntu/Continuum/continuum-be && sh scripts/backup-production.sh >> /home/ubuntu/backup.log 2>&1
```

Copy `~/continuum-backups` off the VM now and then (`scp -r`). It holds every
memory — Personal ones, health included — in plain text.

**Agents** reach the API at `https://my-continuum.duckdns.org/api/v1/...` with an
API key (Settings → API keys) as `Authorization: Bearer ck_...`.

## 8. When something is wrong

| Symptom | Likely cause |
| --- | --- |
| Browser: certificate error, `web` logs ACME failures | DNS does not point at the VM yet, or port 80 is closed (security list or iptables). Fix it, then `docker compose -f production.yml restart web`. |
| Page loads, sign-in says "wrong email or password" | `ADMIN_*` changed after the first start: the admin is created once. Sign in with the original, or create a user from an admin account. |
| `models` job fails | Disk full or the download was interrupted: `df -h`, then `up -d` again — downloads resume. |
| Answers are slow | A 7B model on 4 ARM cores answers at a few tokens a second. `qwen2.5:3b-instruct` in `.api` is faster but a weaker judge: re-run the evaluation corpora before trusting it. |
| Microphone does not work | Browsers allow it only over HTTPS — use the domain, never the IP. |

## What is exposed, and what is not

- **Public:** ports 80 and 443, served by Caddy. 80 only redirects to HTTPS.
- **Private:** the API, Qdrant, Postgres and Ollama listen on the compose network
  alone — no host ports — so no firewall mistake can expose them. SSH (22) stays
  key-only.
- **Your data:** stays on the VM. The LLM, the embedder, Whisper and the voice
  all run there; nothing is sent to a third party.
