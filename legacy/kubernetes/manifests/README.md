# SafeRoute on Kubernetes

Pipeline A runs as a two-replica worker Deployment; Pipeline B runs as a two-replica API Deployment. On a single EC2 node, the Pipeline A replicas share one failure domain: the standby can take over after a pod failure but not after a host failure. The Traefik Ingress exposes `/api` over HTTP for initial verification. Configure DNS and TLS before sending authenticated public requests.

MongoDB indexes are created once by `saferoute-db-indexes` before any application Deployment starts. API pods and Pipeline A do not create indexes at startup, which avoids duplicate and concurrent index creation. For a clean deployment without old Atlas index state, use a new empty database name in the MongoDB URI. The EC2 reset removes only the Kubernetes namespace; it never deletes Atlas data.

## Deploy to a single-node EC2 K3s host

From the project root on your development machine, copy the project without credentials or local dependencies:

```sh
rsync -av --exclude='.git' --exclude='node_modules' --exclude='.venv' \
  --exclude='k8s/secrets.yaml' ./ ubuntu@EC2_PUBLIC_IP:~/saferoute/
```

On Ubuntu EC2, install Docker if needed, then run the deploy script:

```sh
sudo apt-get update
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
cd ~/saferoute
bash scripts/deploy-ec2-k3s.sh
```

The script asks for a MongoDB URI and rotated OpenAQ key without echoing them, generates a random API bearer token, builds and imports both images into K3s, deletes and recreates the Kubernetes `saferoute` namespace after a typed confirmation, runs the one-time index Job, and starts the app Deployments and Ingress. The generated bearer token is stored in `~/saferoute-api-token.txt` with mode `600`. It does not apply the HPA because a single EC2 node has fixed capacity. Apply `backend-hpa.yaml` only after adding enough node capacity and confirming metrics-server is returning metrics.

Use a **new, empty database name** in the MongoDB URI and ensure the Atlas database user has read/write access to it. This leaves the previous database untouched and avoids inherited index definitions. If the index Job fails, the application pods are not started; inspect it with:

```sh
sudo k3s kubectl -n saferoute logs job/saferoute-db-indexes
```

Allow inbound TCP 80 for the initial route check; keep 6443 and 10250 private and restrict SSH. Do not send bearer tokens over HTTP. Point a DNS name at the EC2 public address, configure cert-manager with a Traefik HTTP-01 solver, and enable TLS before authenticated external use. Do not embed a shared bearer token in a public Flutter or web build; use user-specific authentication for public clients.

## Build and publish images to a registry (optional)

For a registry-based deployment, build and push versioned images from the project root:

```sh
docker build -t REGISTRY/saferoute-backend:VERSION .
docker build -t REGISTRY/saferoute-pipeline-a:VERSION -f Pipeline-A/Dockerfile Pipeline-A
docker push REGISTRY/saferoute-backend:VERSION
docker push REGISTRY/saferoute-pipeline-a:VERSION
```

Set those image tags in `backend.yaml`, `pipeline-a.yaml`, and `db-indexes-job.yaml`. For a local cluster such as Kind, load built images into that cluster instead of pushing them.

## Secrets

Do not commit, copy, or bake secrets into images. `k8s/secrets.yaml` is ignored and excluded from the Docker build context. The EC2 deploy script creates the Kubernetes Secret interactively. Rotate credentials that were pasted into chat before using the script. Kubernetes Secrets are base64 encoded by default; enable encryption at rest for etcd and restrict Secret read access with RBAC.

The backend requires an `API_BEARER_TOKEN` of at least 32 characters. Keep the generated token private. A static token embedded in a public frontend can be extracted. Rotate/reissue the OpenAQ key previously pasted into chat.

## Local Kind deployment

From the repository root, run:

```sh
./scripts/start-local-k8s.sh
```

The script starts or creates the two-node Kind cluster, builds and loads both images, validates Secret keys without displaying their values, applies Kubernetes resources, waits for rollouts, and collects redacted diagnostics on failure. Logs are saved under ignored `logs/`. It needs `docker`, `kind`, and `kubectl` on `PATH`, plus a valid local `k8s/secrets.yaml` or an existing `saferoute-secrets` Secret.

For local development, a one-worker Kind cluster has a tainted control-plane node by default. If the Pipeline A standby remains Pending, allow workloads on that node:

```sh
kubectl get nodes
kubectl taint nodes saferoute-control-plane node-role.kubernetes.io/control-plane-
```

## Other deployment notes

- Adjust CPU, memory, MongoDB pool size, and route concurrency for the host and Atlas tier. Single-node placement is not host high availability.
- The optional HPA scales from 2 to 10 at 70% CPU or 80% memory and requires metrics-server plus sufficient capacity. Scaling replicas does not guarantee 10,000 simultaneous requests; provider quotas, Atlas capacity, and load tests determine throughput.
- Route requests and results expire after 30 days by default. Back up records before running `scripts/migrate-route-retention.js --apply`; TTL deletion is asynchronous.
- `localhost` inside a pod refers to that pod, not the EC2 host or your laptop.
