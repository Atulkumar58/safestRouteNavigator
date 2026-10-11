# SafeRoute deployment on one EC2 instance

Pipeline B (the Node.js API) and Pipeline A (the Python data worker) run as two Docker Compose services on the same Ubuntu EC2 instance. MongoDB Atlas remains the shared database. Compose restarts either service after a container or host restart, limits container resource use, health-checks both services, and rotates container logs.

## Prepare EC2

Use Ubuntu 22.04 or 24.04 with at least 2 GiB RAM; 4 GiB gives more room for builds and concurrent route requests. Install Docker Engine and its Compose plugin. Keep SSH restricted to your IP. For initial remote testing, allow inbound TCP 3000 only from the tester's IP. Do not expose Docker's remote API or Kubernetes ports.

Copy the project from your development machine, excluding local dependencies and credentials:

```sh
rsync -av --exclude='.git' --exclude='node_modules' --exclude='.venv' \
  --exclude='Pipeline-A/.venv' --exclude='.secrets/backend.env' \
  --exclude='.secrets/pipeline-a.env' --exclude='.env' \
  --exclude='Pipeline-A/.env' --exclude='legacy' \
  ./ ubuntu@EC2_PUBLIC_IP:~/saferoute/
```

If the instance already has K3s from the earlier setup, first stop SafeRoute and remove K3s on that EC2 host:

```sh
sudo /usr/local/bin/k3s-uninstall.sh
```

This removes the cluster and workloads on that host. It does not remove MongoDB Atlas or its data. Run this only on the EC2 instance you intend to convert.

## Configure credentials

On EC2, from `~/saferoute`:

```sh
mkdir -p .secrets
cp .secrets/backend.env.example .secrets/backend.env
cp .secrets/pipeline-a.env.example .secrets/pipeline-a.env
nano .secrets/backend.env
nano .secrets/pipeline-a.env
chmod 600 .secrets/backend.env .secrets/pipeline-a.env
```

Set the same `MONGODB_URI` in both files, using a new database name if you want a clean schema. Set a newly rotated OpenAQ key in `pipeline-a.env`. Set `API_BEARER_TOKEN` to a newly generated random value of at least 32 characters in `backend.env`; for example, generate one with `openssl rand -hex 32`. Never paste credential values into chat or commit the `.secrets` files. Allow the EC2 public egress address in MongoDB Atlas Network Access and ensure the Atlas database user can read and write the selected database.

## Start both services

```sh
cd ~/saferoute
bash scripts/deploy-ec2-compose.sh
```

The script builds both images, creates MongoDB indexes once, then starts the backend and Pipeline A. The API listens on port 3000. Pipeline A's health endpoint is bound to localhost on port 8081.

## Check status, logs, and health

```sh
docker compose ps
docker compose logs -f --tail=100
docker compose logs -f backend
docker compose logs -f pipeline-a
curl -i http://localhost:3000/health/ready
curl -i http://localhost:8081/health/ready
```

From another machine, check `http://EC2_PUBLIC_IP:3000/health/ready`. To request routes, send `POST http://EC2_PUBLIC_IP:3000/api/routes` with JSON and `Authorization: Bearer <API_BEARER_TOKEN>`; the request also needs a unique `Idempotency-Key` header. Keep the token out of public frontend code. For a public app, put HTTPS and user authentication in a trusted server or reverse proxy before exposing route requests broadly; the EC2 security group should expose only the ports required by that setup.

After changing code or environment settings, run `bash scripts/deploy-ec2-compose.sh` again. To stop SafeRoute, run `docker compose down` from the project directory. This leaves images, environment files, and MongoDB data intact. To start it later, run `docker compose up -d`.

## Resource and availability notes

Both services share one EC2 host, so a host outage stops both. Docker Compose brings them back when the host returns, but it is not host failover. Container memory limits are 768 MiB each and CPU limits are one core each. Tune these with actual memory and request measurements. This configuration is a straightforward single-instance deployment; it does not claim capacity for 10,000 concurrent users.

MongoDB indexes are created by one explicit command before the services start. The Pipeline A worker has a MongoDB lease, waits 10 minutes after a successful pass, and retries incomplete passes after a short backoff. Its periodic polling is logged by Docker.
