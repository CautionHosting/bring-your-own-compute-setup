#!/usr/bin/env python3
"""Targeted tests for VPC/subnet selection in setup.py (moto-mocked AWS).

Covers the review-feedback fixes and the existing-VPC path:
  1. SUBNET_IDS without VPC_ID -> exit 1 before creating anything
  2. Foreign subnet (not in VPC) -> exit 1
  3. Duplicate subnet IDs deduped, order preserved in credentials.json
  4. VPC_ID alone -> all VPC subnets used
  5. IAM policy is VPC-scoped when VPC_ID set, unscoped otherwise
  6. Teardown skips user-provided VPCs (tag-based discovery finds nothing)

Run: uv run --with boto3 --with moto --with pytest pytest tests/test_vpc_selection.py
"""

import importlib.util
import json
import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

SETUP_PY = Path(__file__).resolve().parent.parent / "setup.py"

REGION = "us-west-2"


def load_setup_module():
    spec = importlib.util.spec_from_file_location("setup_provisioner", SETUP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_teardown_module(name):
    teardown_src = SETUP_PY.parent / "teardown.py"
    spec = importlib.util.spec_from_file_location(name, teardown_src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def aws():
    """Full moto mock over all services setup.py touches."""
    with mock_aws():
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_REGION"] = REGION
        os.environ["AWS_DEFAULT_REGION"] = REGION
        yield
        for k in ("VPC_ID", "SUBNET_IDS", "OUTPUT_FILE"):
            os.environ.pop(k, None)


@pytest.fixture
def existing_vpc(aws):
    """A user-provided VPC (no caution scope tags) with two subnets in different AZs."""
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.99.0.0/16")["Vpc"]["VpcId"]
    subnets = []
    for i, cidr in enumerate(("10.99.1.0/24", "10.99.2.0/24")):
        s = ec2.create_subnet(
            VpcId=vpc,
            CidrBlock=cidr,
            AvailabilityZone=f"{REGION}a" if i == 0 else f"{REGION}b",
        )
        subnets.append(s["Subnet"]["SubnetId"])
    return {"vpc_id": vpc, "subnet_ids": subnets}


@pytest.fixture
def output_file(tmp_path):
    f = tmp_path / "credentials.json"
    os.environ["OUTPUT_FILE"] = str(f)
    return f


def run_setup(expect_success=True):
    """Run setup.py main() in-process with moto active."""
    mod = load_setup_module()
    if expect_success:
        mod.main()
    else:
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1, "expected exit code 1"


# -- 1. SUBNET_IDS without VPC_ID ----------------------------------------


def test_subnet_ids_without_vpc_id_fails_before_creating_resources(aws, output_file):
    os.environ["SUBNET_IDS"] = "subnet-aaa"
    run_setup(expect_success=False)
    assert not output_file.exists(), "no credentials.json should be written"
    ec2 = boto3.client("ec2", region_name=REGION)
    # moto pre-seeds a default VPC; the invariant is that setup created nothing
    # new (no caution-tagged VPC).
    scoped = [
        v
        for v in ec2.describe_vpcs()["Vpcs"]
        for t in v.get("Tags", [])
        if t["Key"] == "caution:deployment-id"
    ]
    assert scoped == [], "no scoped VPC may be created when config is invalid"


# -- 2. Foreign subnet rejected -------------------------------------------


def test_foreign_subnet_rejected(aws, output_file, existing_vpc):
    os.environ["VPC_ID"] = existing_vpc["vpc_id"]
    os.environ["SUBNET_IDS"] = "subnet-0deadbeefdeadbeef"
    run_setup(expect_success=False)
    assert not output_file.exists()


def test_subnet_from_different_vpc_rejected(aws, output_file, existing_vpc):
    ec2 = boto3.client("ec2", region_name=REGION)
    other_vpc = ec2.create_vpc(CidrBlock="10.98.0.0/16")["Vpc"]["VpcId"]
    other_subnet = ec2.create_subnet(
        VpcId=other_vpc, CidrBlock="10.98.1.0/24", AvailabilityZone=f"{REGION}a"
    )["Subnet"]["SubnetId"]
    os.environ["VPC_ID"] = existing_vpc["vpc_id"]
    os.environ["SUBNET_IDS"] = other_subnet
    run_setup(expect_success=False)
    assert not output_file.exists()


# -- 3. Dedupe + order preservation ----------------------------------------


def test_duplicate_subnets_deduped_order_preserved(aws, output_file, existing_vpc):
    a, b = existing_vpc["subnet_ids"]
    os.environ["VPC_ID"] = existing_vpc["vpc_id"]
    os.environ["SUBNET_IDS"] = f"{b},{a},{b},{a}"
    run_setup()
    creds = json.loads(output_file.read_text())
    assert creds["subnet_ids"] == [b, a], "duplicates removed, first-seen order kept"
    assert creds["vpc_id"] == existing_vpc["vpc_id"]


# -- 4. VPC_ID alone uses all subnets ---------------------------------------


def test_vpc_id_alone_uses_all_subnets(aws, output_file, existing_vpc):
    os.environ["VPC_ID"] = existing_vpc["vpc_id"]
    run_setup()
    creds = json.loads(output_file.read_text())
    assert sorted(creds["subnet_ids"]) == sorted(existing_vpc["subnet_ids"])


# -- 5. IAM policy VPC scoping -----------------------------------------------


def test_iam_policy_vpc_scoped_when_vpc_id_set(aws, output_file, existing_vpc):
    os.environ["VPC_ID"] = existing_vpc["vpc_id"]
    mod = load_setup_module()
    account = boto3.client("sts").get_caller_identity()["Account"]
    doc = mod.create_scoped_iam_policy(
        account, REGION, "depid", "bucket", existing_vpc["vpc_id"]
    )
    assert f"arn:aws:ec2:{REGION}:{account}:vpc/{existing_vpc['vpc_id']}" in json.dumps(doc)


def test_iam_policy_unscoped_without_vpc_id(aws, output_file):
    mod = load_setup_module()
    account = boto3.client("sts").get_caller_identity()["Account"]
    doc = mod.create_scoped_iam_policy(account, REGION, "depid", "bucket", None)
    assert "ec2:Vpc" not in json.dumps(doc)


# -- 6. Teardown never touches user-provided VPCs -----------------------------


def test_teardown_skips_untagged_vpc(aws, existing_vpc):
    mod = load_teardown_module("teardown_provisioner")

    ec2 = boto3.client("ec2", region_name=REGION)
    mod.delete_vpc_resources(ec2, "somedeploymentid1234")
    # Untagged (user-provided) VPC must still exist
    vpcs = ec2.describe_vpcs(VpcIds=[existing_vpc["vpc_id"]])["Vpcs"]
    assert vpcs, "user-provided VPC must survive teardown"


def test_teardown_deletes_tagged_vpc(aws):
    mod = load_setup_module()
    ec2 = boto3.client("ec2", region_name=REGION)
    dep_id = "abcdef0123456789"
    vpc_id, _ = mod.create_vpc(ec2, dep_id, REGION)

    tmod = load_teardown_module("teardown_provisioner2")
    tmod.delete_vpc_resources(ec2, dep_id)
    with pytest.raises(Exception):
        ec2.describe_vpcs(VpcIds=[vpc_id])
