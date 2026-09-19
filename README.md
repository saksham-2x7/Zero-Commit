# Deadman

Transactional AWS security-group changes that auto-revert unless a human confirms.

## Setup and Deploy
1. Run `./scripts/deploy_shared.sh` to deploy the shared stack.
2. Run `./scripts/deploy_dev.sh <stage>` to deploy a dev stack.
3. Run `./scripts/deploy_web.sh <stage>` to build and sync the frontend.
