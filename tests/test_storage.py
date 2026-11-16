import io

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.config import settings
from app.storage import S3Storage, StorageError, StoredObject, storage_settings

BUCKET = "contentgun"
VALUES = {
    "s3_endpoint": "http://127.0.0.1:9000",
    "s3_region": "us-east-1",
    "s3_bucket": BUCKET,
    "s3_access_key": "access",
    "s3_secret_key": "secret",
    "public_files_url": "http://127.0.0.1:8010/",
}


@pytest.fixture
def configured(monkeypatch):
    for name, value in VALUES.items():
        monkeypatch.setattr(settings, name, value)


def test_without_any_setting_there_is_no_storage():
    assert storage_settings() is None


def test_all_settings_make_a_storage(configured):
    config = storage_settings()
    assert config is not None
    assert config.bucket == BUCKET
    assert config.endpoint == "http://127.0.0.1:9000"
    assert config.public_files_url == "http://127.0.0.1:8010"
    assert config.ttl_hours == 24


@pytest.mark.parametrize("missing", list(VALUES))
def test_a_missing_setting_stops_the_start(configured, monkeypatch, missing):
    monkeypatch.setattr(settings, missing, "  ")
    with pytest.raises(RuntimeError) as excinfo:
        storage_settings()
    assert f"but {missing.upper()} is empty." in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("s3_endpoint", "127.0.0.1:9000", "S3_ENDPOINT must be an http:// or https:// URL"),
        ("public_files_url", "ftp://files.test", "PUBLIC_FILES_URL must be an http:// or"),
        ("public_files_url", "https://files.test/?a=1", "must not carry a query or a fragment"),
        ("s3_bucket", "Contentgun_Files", "is not a valid bucket name"),
    ],
)
def test_an_invalid_setting_stops_the_start(configured, monkeypatch, name, value, message):
    monkeypatch.setattr(settings, name, value)
    with pytest.raises(RuntimeError) as excinfo:
        storage_settings()
    assert message in str(excinfo.value)


@pytest.mark.parametrize(("hours", "days"), [(24, 2), (25, 3), (1, 2), (168, 8)])
def test_the_lifecycle_rule_outlasts_every_stored_file(configured, monkeypatch, hours, days):
    monkeypatch.setattr(settings, "storage_ttl_hours", hours)
    assert storage_settings().lifecycle_days == days


@pytest.fixture
def s3():
    storage = S3Storage("http://127.0.0.1:9000", "us-east-1", BUCKET, "access", "secret")
    with Stubber(storage.client) as stubber:
        yield storage, stubber
        stubber.assert_no_pending_responses()


def test_the_client_addresses_by_path_and_adds_checksums_only_where_required(s3):
    config = s3[0].client.meta.config
    assert config.s3 == {"addressing_style": "path"}
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"


def test_put_sends_type_and_metadata(s3):
    storage, stubber = s3
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": BUCKET,
            "Key": "outputs/acme/t/card.png",
            "Body": b"png",
            "ContentType": "image/png",
            "Metadata": {"expires-at": "1"},
        },
    )
    storage.put("outputs/acme/t/card.png", b"png", "image/png", {"expires-at": "1"})


def test_head_describes_an_object(s3):
    storage, stubber = s3
    stubber.add_response(
        "head_object",
        {"ContentLength": 3, "ContentType": "image/png", "Metadata": {"expires-at": "9"}},
        {"Bucket": BUCKET, "Key": "k"},
    )
    assert storage.head("k") == StoredObject("k", 3, "image/png", {"expires-at": "9"})


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_a_missing_object_is_none(s3, code):
    storage, stubber = s3
    stubber.add_client_error("head_object", service_error_code=code, http_status_code=404)
    stubber.add_client_error("get_object", service_error_code=code, http_status_code=404)
    assert storage.head("k") is None
    assert storage.get("k") is None


def test_any_other_error_is_a_storage_error(s3):
    storage, stubber = s3
    stubber.add_client_error("head_object", service_error_code="403", http_status_code=403)
    with pytest.raises(StorageError) as excinfo:
        storage.head("k")
    assert str(excinfo.value) == f"Reading s3://{BUCKET}/k failed: 403"


def test_the_random_part_of_a_key_stays_out_of_the_error(s3):
    storage, stubber = s3
    stubber.add_client_error("head_object", service_error_code="500", http_status_code=500)
    with pytest.raises(StorageError) as excinfo:
        storage.head("outputs/acme/secret-token/card.png")
    assert "secret-token" not in str(excinfo.value)
    assert f"s3://{BUCKET}/outputs/acme/…" in str(excinfo.value)


def test_an_unreachable_bucket_is_a_storage_error(s3, monkeypatch):
    storage, _ = s3

    def refuse(**kwargs):
        raise EndpointConnectionError(endpoint_url="http://127.0.0.1:9000")

    monkeypatch.setattr(storage.client, "put_object", refuse)
    with pytest.raises(StorageError) as excinfo:
        storage.put("k", b"x", "image/png", {})
    assert "EndpointConnectionError" in str(excinfo.value)


def test_get_reads_the_whole_body(s3):
    storage, stubber = s3
    body = StreamingBody(io.BytesIO(b"hello"), 5)
    stubber.add_response(
        "get_object",
        {"Body": body, "ContentLength": 5, "ContentType": "image/png"},
        {"Bucket": BUCKET, "Key": "k"},
    )
    assert storage.get("k") == b"hello"


def test_open_streams_the_body(s3):
    storage, stubber = s3
    body = StreamingBody(io.BytesIO(b"hello"), 5)
    stubber.add_response(
        "get_object",
        {"Body": body, "ContentLength": 5, "ContentType": "application/pdf", "Metadata": {}},
        {"Bucket": BUCKET, "Key": "k"},
    )
    stored, chunks = storage.open("k")
    assert stored == StoredObject("k", 5, "application/pdf", {})
    assert b"".join(chunks) == b"hello"


def test_keys_follow_every_page(s3):
    storage, stubber = s3
    stubber.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "templates/a"}], "IsTruncated": True, "NextContinuationToken": "t"},
        {"Bucket": BUCKET, "Prefix": "templates/"},
    )
    stubber.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "templates/b"}], "IsTruncated": False},
        {"Bucket": BUCKET, "Prefix": "templates/", "ContinuationToken": "t"},
    )
    assert storage.keys("templates/") == ["templates/a", "templates/b"]


def test_an_existing_bucket_is_left_alone(s3):
    storage, stubber = s3
    stubber.add_response("head_bucket", {}, {"Bucket": BUCKET})
    assert storage.ensure_bucket() is False


def test_a_missing_bucket_is_created(s3):
    storage, stubber = s3
    stubber.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stubber.add_response("create_bucket", {}, {"Bucket": BUCKET})
    assert storage.ensure_bucket() is True


def test_the_lifecycle_rules_expire_each_prefix(s3):
    storage, stubber = s3
    rules = [
        {
            "ID": f"contentgun-{name}",
            "Status": "Enabled",
            "Filter": {"Prefix": f"{name}/"},
            "Expiration": {"Days": 2},
        }
        for name in ("outputs", "uploads")
    ]
    stubber.add_response(
        "put_bucket_lifecycle_configuration",
        {},
        {"Bucket": BUCKET, "LifecycleConfiguration": {"Rules": rules}},
    )
    storage.expire(["outputs/", "uploads/"], 2)
