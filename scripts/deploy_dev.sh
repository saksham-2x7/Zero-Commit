#!/bin/bash
sam build -t infra/template.yaml
sam deploy --stack-name deadman-$1 --parameter-overrides Stage=$1 AuthToken=$AUTH_TOKEN --capabilities CAPABILITY_IAM --resolve-s3
