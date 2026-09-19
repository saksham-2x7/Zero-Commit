#!/bin/bash
sam build -t infra/template.yaml
sam deploy --stack-name deadman-shared --parameter-overrides Stage=shared AuthToken=$AUTH_TOKEN --capabilities CAPABILITY_IAM --resolve-s3
