"""S3 authentication examples for OmniFetcher.

This file demonstrates AWS S3 authentication:
- AWS credentials authentication
- Using S3 fetcher with auth
- Environment variable based auth

Prerequisites:
- boto3 package installed
- AWS credentials configured (via env vars, config file, or IAM role)

Run with: python 08_s3_auth_example.py
"""

import asyncio
import os

from omni_fetcher import OmniFetcher
from omni_fetcher.auth import AuthConfig
from omni_fetcher.fetchers.s3 import S3Fetcher


async def s3_direct_credentials_example():
    """Example 1: S3 authentication with direct credentials."""
    print("\n" + "=" * 60)
    print("Example 1: S3 with Direct Credentials")
    print("=" * 60)

    # Create S3 fetcher with direct credentials
    fetcher = S3Fetcher(
        aws_access_key_id="YOUR_ACCESS_KEY_ID",  # Replace with actual key
        aws_secret_access_key="YOUR_SECRET_ACCESS_KEY",  # Replace with actual secret
        region_name="us-east-1",
    )

    print("S3 Fetcher configured with:")
    print(
        f"  AWS Access Key ID: {fetcher.aws_access_key_id[:8]}..."
        if fetcher.aws_access_key_id
        else "  AWS Access Key ID: None"
    )
    print(f"  AWS Region: {fetcher.region_name}")
    print("\nNote: For production, use IAM roles or environment variables")


async def s3_auth_config_example():
    """Example 2: S3 with AuthConfig."""
    print("\n" + "=" * 60)
    print("Example 2: S3 with AuthConfig")
    print("=" * 60)

    # Create AuthConfig for AWS
    auth = AuthConfig(
        type="aws",
        aws_access_key_id="YOUR_ACCESS_KEY_ID",  # Replace with actual key
        aws_secret_access_key="YOUR_SECRET_ACCESS_KEY",  # Replace with actual secret
        aws_region="us-west-2",
    )

    # Create S3 fetcher and set auth
    fetcher = S3Fetcher()
    fetcher.set_auth(auth)

    print("AuthConfig applied to S3Fetcher:")
    print(f"  Auth type: {auth.type}")
    print(
        f"  Access Key ID: {auth.get_aws_credentials().get('aws_access_key_id', 'None')[:8]}..."
        if auth.get_aws_credentials().get("aws_access_key_id")
        else "  Access Key ID: None"
    )
    print(f"  Region: {fetcher.region_name}")

    # Get headers (S3 doesn't use HTTP headers for auth, but this shows the config)
    print(f"\n  AWS Credentials: {list(auth.get_aws_credentials().keys())}")


async def s3_env_credentials_example():
    """Example 3: S3 with environment variable credentials."""
    print("\n" + "=" * 60)
    print("Example 3: S3 with Environment Variables")
    print("=" * 60)

    # Set environment variables for AWS (standard AWS SDK behavior)
    # These are the standard AWS environment variables
    os.environ["AWS_ACCESS_KEY_ID"] = "env_access_key_id"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "env_secret_access_key"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    # Create S3 fetcher without explicit credentials
    # It will automatically use environment variables
    fetcher = S3Fetcher()

    print("S3 Fetcher using environment variables:")
    print("  AWS_ACCESS_KEY_ID env var detected: 'AWS_ACCESS_KEY_ID' in os.environ")
    print("  AWS_SECRET_ACCESS_KEY env var detected: 'AWS_SECRET_ACCESS_KEY' in os.environ")
    print("  AWS_DEFAULT_REGION env var detected: 'AWS_DEFAULT_REGION' in os.environ")
    print(f"\n  Fetcher region (from env): {fetcher.region_name}")

    # With AuthConfig using env var names
    auth = AuthConfig(
        type="aws",
        aws_access_key_id_env="AWS_ACCESS_KEY_ID",
        aws_secret_access_key_env="AWS_SECRET_ACCESS_KEY",
        aws_region_env="AWS_DEFAULT_REGION",
    )

    creds = auth.get_aws_credentials()
    print(f"\n  Via AuthConfig:")
    print(f"    Access Key: {creds.get('aws_access_key_id')}")
    print(f"    Region: {creds.get('region_name')}")

    # Clean up
    del os.environ["AWS_ACCESS_KEY_ID"]
    del os.environ["AWS_SECRET_ACCESS_KEY"]
    del os.environ["AWS_DEFAULT_REGION"]


async def s3_with_omni_fetcher_example():
    """Example 4: Using S3 with OmniFetcher."""
    print("\n" + "=" * 60)
    print("Example 4: S3 with OmniFetcher")
    print("=" * 60)

    # Create OmniFetcher with S3 auth
    fetcher = OmniFetcher(
        load_env_auth=False,
        auth={
            "s3": {
                "type": "aws",
                "aws_access_key_id_env": "AWS_ACCESS_KEY_ID",
                "aws_secret_access_key_env": "AWS_SECRET_ACCESS_KEY",
                "aws_region": "us-east-1",
            },
        },
    )

    # Check the auth config
    auth = fetcher.get_auth("s3")
    if auth:
        print("S3 auth configured in OmniFetcher:")
        print(f"  Type: {auth.type}")
        print(f"  Access Key ID env: {auth.aws_access_key_id_env}")
        print(f"  Secret Key env: {auth.aws_secret_access_key_env}")
        print(f"  Region: {auth.aws_region}")

        print("\nUsage with fetch():")
        print("  # Set credentials via environment variables")
        print("  import os")
        print("  os.environ['AWS_ACCESS_KEY_ID'] = 'your_key'")
        print("  os.environ['AWS_SECRET_ACCESS_KEY'] = 'your_secret'")
        print("  ")
        print("  # Fetch from S3")
        print("  result = await fetcher.fetch('s3://bucket-name/path/to/file.txt')")


async def s3_env_loading_example():
    """Example 5: Loading S3 auth from environment variables via prefix."""
    print("\n" + "=" * 60)
    print("Example 5: S3 Auth via OmniFetcher Environment Loading")
    print("=" * 60)

    # Set up OmniFetcher-prefixed environment variables
    # These follow the pattern: OMNI_SOURCE_PROPERTY
    os.environ["OMNI_S3_TYPE"] = "aws"
    os.environ["OMNI_S3_AWS_ACCESS_KEY_ID_ENV"] = "S3_ACCESS_KEY"
    os.environ["OMNI_S3_AWS_SECRET_ACCESS_KEY_ENV"] = "S3_SECRET_KEY"
    os.environ["OMNI_S3_AWS_REGION"] = "ap-southeast-1"

    # Set the actual credentials
    os.environ["S3_ACCESS_KEY"] = "AKIAIOSFODNN7EXAMPLE"
    os.environ["S3_SECRET_KEY"] = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    # Create fetcher - auth will be loaded automatically
    fetcher = OmniFetcher(load_env_auth=True)

    auth = fetcher.get_auth("s3")
    if auth:
        print("S3 auth loaded from environment:")
        print(f"  Type: {auth.type}")

        creds = auth.get_aws_credentials()
        print(f"  Access Key: {creds.get('aws_access_key_id')}")
        print(f"  Region: {creds.get('region_name')}")

        print("\nEnvironment variables set:")
        print("  OMNI_S3_TYPE=aws")
        print("  OMNI_S3_AWS_ACCESS_KEY_ID_ENV=S3_ACCESS_KEY")
        print("  OMNI_S3_AWS_SECRET_ACCESS_KEY_ENV=S3_SECRET_KEY")
        print("  OMNI_S3_AWS_REGION=ap-southeast-1")
        print("  S3_ACCESS_KEY=<your_key>")
        print("  S3_SECRET_KEY=<your_secret>")

    # Clean up
    for key in [
        "OMNI_S3_TYPE",
        "OMNI_S3_AWS_ACCESS_KEY_ID_ENV",
        "OMNI_S3_AWS_SECRET_ACCESS_KEY_ENV",
        "OMNI_S3_AWS_REGION",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
    ]:
        if key in os.environ:
            del os.environ[key]


async def s3_iam_role_example():
    """Example 6: S3 with IAM role (no explicit credentials)."""
    print("\n" + "=" * 60)
    print("Example 6: S3 with IAM Role (No Credentials)")
    print("=" * 60)

    # Create S3 fetcher without credentials
    # When running on EC2, Lambda, or ECS with IAM role,
    # boto3 will automatically use the IAM role credentials
    fetcher = S3Fetcher()

    print("S3 Fetcher without explicit credentials:")
    print("  When running on AWS (EC2, Lambda, ECS):")
    print("    - Credentials are obtained from IAM role automatically")
    print("    - No need to set access keys")
    print("  ")
    print("  Usage:")
    print("    fetcher = S3Fetcher()  # No credentials needed")
    print("    # boto3 handles IAM role automatically")
    print("  ")
    print("  For local development, use:")
    print("    - AWS CLI: aws configure")
    print("    - Shared credentials file: ~/.aws/credentials")
    print("    - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")


async def main():
    """Run all S3 authentication examples."""
    print("OmniFetcher S3 Authentication Examples")
    print("=" * 60)

    await s3_direct_credentials_example()
    await s3_auth_config_example()
    await s3_env_credentials_example()
    await s3_with_omni_fetcher_example()
    await s3_env_loading_example()
    await s3_iam_role_example()

    print("\n" + "=" * 60)
    print("All S3 authentication examples completed!")
    print("=" * 60)
    print("\nFor production use:")
    print("  1. Use IAM roles on AWS services (EC2, Lambda, ECS)")
    print("  2. Use environment variables for local development")
    print("  3. Use AWS config files (~/.aws/credentials)")
    print("  4. Never commit credentials to version control")


if __name__ == "__main__":
    asyncio.run(main())
