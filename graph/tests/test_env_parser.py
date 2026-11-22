"""Unit contract for `parse_service_refs`.

Rows 23-33 of the M3 behaviour list. Only Kubernetes service DNS names count as
references: a bare IP, an external host, an empty value or a non-URL string must
produce nothing, because inventing a dependency is worse than missing one.
"""

from __future__ import annotations

from env_parser import parse_service_refs


def test_full_dns_url_parses_to_name_namespace_and_env_var() -> None:
    refs = parse_service_refs({"DB_URL": "http://db.shop.svc.cluster.local"})

    assert refs == [{"name": "db", "namespace": "shop", "env_var": "DB_URL"}]


def test_port_suffix_is_accepted() -> None:
    refs = parse_service_refs({"DB_URL": "http://db.shop.svc.cluster.local:5432"})

    assert refs == [{"name": "db", "namespace": "shop", "env_var": "DB_URL"}]


def test_path_suffix_is_accepted() -> None:
    refs = parse_service_refs({"DB_URL": "http://db.shop.svc.cluster.local/api/v1"})

    assert refs == [{"name": "db", "namespace": "shop", "env_var": "DB_URL"}]


def test_short_service_form_is_accepted() -> None:
    refs = parse_service_refs({"DB_URL": "http://db.shop.svc"})

    assert refs == [{"name": "db", "namespace": "shop", "env_var": "DB_URL"}]


def test_https_is_accepted() -> None:
    refs = parse_service_refs({"DB_URL": "https://db.shop.svc"})

    assert refs == [{"name": "db", "namespace": "shop", "env_var": "DB_URL"}]


def test_multiple_refs_yield_one_entry_per_matching_var() -> None:
    refs = parse_service_refs(
        {
            "A_URL": "http://a.shop.svc",
            "B_URL": "http://b.shop.svc",
            "FLAG": "true",
        }
    )

    assert sorted(refs, key=lambda ref: ref["name"]) == [
        {"name": "a", "namespace": "shop", "env_var": "A_URL"},
        {"name": "b", "namespace": "shop", "env_var": "B_URL"},
    ]


def test_bare_ip_yields_nothing() -> None:
    assert parse_service_refs({"HOST": "10.0.0.5"}) == []


def test_external_url_yields_nothing() -> None:
    assert parse_service_refs({"API": "https://api.stripe.com/v1"}) == []


def test_empty_value_yields_nothing() -> None:
    assert parse_service_refs({"DB_URL": ""}) == []


def test_empty_env_yields_nothing() -> None:
    assert parse_service_refs({}) == []


def test_non_url_strings_yield_nothing() -> None:
    assert parse_service_refs({"DEBUG": "true", "PORT": "8000"}) == []
