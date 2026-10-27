#!/usr/bin/env python3
"""
bootstrap_tf_backend.py — Creates the S3 bucket and DynamoDB table for
Terraform remote state BEFORE running terraform init.

Run this ONCE with AWS credentials that have S3 and DynamoDB permissions:
    python scripts/bootstrap_tf_backend.py [--bucket BUCKET] [--table TABLE] [--region REGION]
"""

import argparse
import boto3
import sys
from botocore.exceptions import ClientError

DEFAULT_BUCKET = "cost-gate-tf-state"
DEFAULT_TABLE  = "cost-gate-tf-locks"
DEFAULT_REGION = "us-east-1"


def create_s3_bucket(s3, region: str, bucket: str) -> None:
    print(f"Creating S3 bucket: {bucket} in {region}")
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        # Enable versioning
        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        # Enable encryption
        s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
        # Block public access
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls":       True,
                "IgnorePublicAcls":      True,
                "BlockPublicPolicy":     True,
                "RestrictPublicBuckets": True,
            },
        )
        print(f"  ✅ S3 bucket '{bucket}' created with versioning + encryption")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            print(f"  ℹ️  S3 bucket '{bucket}' already exists — skipping")
        else:
            print(f"  ❌ Error: {e}", file=sys.stderr)
            raise


def create_dynamodb_table(ddb, table: str) -> None:
    print(f"Creating DynamoDB table: {table}")
    try:
        ddb.create_table(
            TableName=table,
            KeySchema=[{"AttributeName": "LockID", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "LockID", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
            Tags=[
                {"Key": "Project", "Value": "cost-aware-gitops-gate"},
                {"Key": "Purpose", "Value": "terraform-state-locking"},
            ],
        )
        ddb.get_waiter("table_exists").wait(TableName=table)
        print(f"  ✅ DynamoDB table '{table}' created")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"  ℹ️  DynamoDB table '{table}' already exists — skipping")
        else:
            print(f"  ❌ Error: {e}", file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser(description="Bootstrap Terraform S3+DynamoDB backend")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--table",  default=DEFAULT_TABLE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args()

    s3  = boto3.client("s3",      region_name=args.region)
    ddb = boto3.client("dynamodb", region_name=args.region)

    create_s3_bucket(s3, args.region, args.bucket)
    create_dynamodb_table(ddb, args.table)

    print(f"""
✅ Backend bootstrap complete!

Next steps:
  1. Update infrastructure/main.tf backend block:
       bucket         = "{args.bucket}"
       dynamodb_table = "{args.table}"
       region         = "{args.region}"

  2. Run:
       cd infrastructure
       cp terraform.tfvars.example terraform.tfvars
       # Edit terraform.tfvars with your github_repo
       terraform init
       terraform plan
       terraform apply
""")


if __name__ == "__main__":
    main()
