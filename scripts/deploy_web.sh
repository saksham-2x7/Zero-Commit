#!/bin/bash
STAGE=${1:-shared}
cd web || exit 1
npm ci
npm run build
BUCKET_NAME=$(aws cloudformation describe-stacks --stack-name deadman-${STAGE} --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketOut'].OutputValue" --output text)
DIST_ID=$(aws cloudformation describe-stacks --stack-name deadman-${STAGE} --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" --output text | awk -F. '{print $1}')
aws s3 sync dist/ s3://${BUCKET_NAME}/ --delete
# invalidate cloudfront
aws cloudfront create-invalidation --distribution-id ${DIST_ID} --paths "/*"
