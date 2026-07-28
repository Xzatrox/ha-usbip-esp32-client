# Feature: ha-usbip-esp32-client, Property 14: X-Ingress-Path URL generation
"""Property tests verifying X-Ingress-Path handling in the Flask WebUI.

For any HTTP request containing an X-Ingress-Path header value, all URLs
generated in the response (navigation links, API endpoint references, static
asset paths) SHALL be prefixed with that ingress path value.

Since templates are not yet created (task 6.3), this test focuses on verifying:
1. The before_request hook correctly extracts X-Ingress-Path into g.ingress_path
2. Page routes pass the ingress_path variable to templates correctly
3. The ingress path is available in the request context for any path value

**Validates: Requirements 16.9**
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from usbip_addon.webui.app import create_app


# --- Strategies ---

# Generate valid ingress path segments (URL path components)
path_segment_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_",
    min_size=1,
    max_size=20,
)

# Generate ingress paths: empty string, single segment, or multi-segment paths
# Ingress paths from HA look like "/api/hassio/ingress/<token>"
ingress_path_strategy = st.one_of(
    # Empty string (no ingress)
    st.just(""),
    # Single segment path like "/ingress"
    path_segment_strategy.map(lambda s: f"/{s}"),
    # Multi-segment paths like "/api/hassio/ingress/abc123"
    st.lists(path_segment_strategy, min_size=2, max_size=5).map(
        lambda segments: "/" + "/".join(segments)
    ),
)

# Generate typical HA ingress paths specifically
ha_ingress_path_strategy = st.tuples(
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
        min_size=8,
        max_size=32,
    ),
).map(lambda t: f"/api/hassio/ingress/{t[0]}")

# Combined strategy covering all ingress path variants
all_ingress_paths_strategy = st.one_of(
    ingress_path_strategy,
    ha_ingress_path_strategy,
)

# Page routes that exist in the app
page_routes_strategy = st.sampled_from(["/", "/devices", "/logs", "/events"])


def _create_test_app():
    """Create a Flask test app with mocked dependencies."""
    config = MagicMock()
    config.devices = []
    config.read_config = MagicMock()
    config.notifications_enabled = False

    usbip_client = MagicMock()
    usbip_client.list_ports = MagicMock(return_value=[])

    health_checker = MagicMock()
    discovery = MagicMock()
    event_log = MagicMock()
    event_log.read_events = MagicMock(return_value=[])
    server_locks = MagicMock()
    server_locks.lock = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=None),
        __exit__=MagicMock(return_value=False),
    ))

    app = create_app(
        config=config,
        usbip_client=usbip_client,
        health_checker=health_checker,
        discovery=discovery,
        event_log=event_log,
        server_locks=server_locks,
    )
    app.config["TESTING"] = True
    return app


# --- Property 14: X-Ingress-Path URL generation ---


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(ingress_path=all_ingress_paths_strategy)
def test_ingress_path_stored_in_g(ingress_path: str):
    """For any HTTP request containing an X-Ingress-Path header value,
    g.ingress_path SHALL be set to that exact header value.

    **Validates: Requirements 16.9**
    """
    app = _create_test_app()

    with app.test_request_context(
        "/api/status",
        headers={"X-Ingress-Path": ingress_path},
    ):
        # Trigger before_request hooks
        app.preprocess_request()

        from flask import g
        assert g.ingress_path == ingress_path, (
            f"Expected g.ingress_path to be '{ingress_path}', "
            f"but got '{g.ingress_path}'"
        )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(ingress_path=all_ingress_paths_strategy)
def test_missing_header_defaults_to_empty(ingress_path: str):
    """When the X-Ingress-Path header is absent, g.ingress_path SHALL
    default to an empty string.

    **Validates: Requirements 16.9**
    """
    app = _create_test_app()

    with app.test_request_context("/api/status"):
        # No X-Ingress-Path header set
        app.preprocess_request()

        from flask import g
        assert g.ingress_path == "", (
            f"Expected g.ingress_path to be '' when header is absent, "
            f"but got '{g.ingress_path}'"
        )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    ingress_path=all_ingress_paths_strategy,
    route=page_routes_strategy,
)
def test_page_routes_receive_ingress_path(ingress_path: str, route: str):
    """For any page route accessed with an X-Ingress-Path header, the
    template render call SHALL receive ingress_path as a template variable
    equal to the header value.

    **Validates: Requirements 16.9**
    """
    app = _create_test_app()
    client = app.test_client()

    captured_context = {}

    def mock_render_template(template_name, **kwargs):
        captured_context["template"] = template_name
        captured_context["ingress_path"] = kwargs.get("ingress_path")
        return f"<!-- rendered {template_name} -->"

    with patch("usbip_addon.webui.app.render_template", side_effect=mock_render_template):
        response = client.get(
            route,
            headers={"X-Ingress-Path": ingress_path},
        )

    # The route should succeed (render_template was called)
    assert response.status_code == 200, (
        f"Expected 200 for route {route}, got {response.status_code}"
    )

    # Verify ingress_path was passed to the template
    assert "ingress_path" in captured_context, (
        f"render_template was not called with ingress_path for route {route}"
    )
    assert captured_context["ingress_path"] == ingress_path, (
        f"Expected ingress_path='{ingress_path}' passed to template, "
        f"but got '{captured_context['ingress_path']}' for route {route}"
    )


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    ingress_path=all_ingress_paths_strategy,
)
def test_api_routes_accessible_with_ingress_path(ingress_path: str):
    """API endpoints SHALL function correctly regardless of X-Ingress-Path
    header value (APIs return JSON, not HTML with links).

    **Validates: Requirements 16.9**
    """
    app = _create_test_app()
    client = app.test_client()

    # Test /api/status endpoint works with any ingress path
    response = client.get(
        "/api/status",
        headers={"X-Ingress-Path": ingress_path},
    )

    assert response.status_code == 200, (
        f"Expected /api/status to return 200 with ingress_path='{ingress_path}', "
        f"got {response.status_code}"
    )

    data = response.get_json()
    assert data is not None, "Response should be valid JSON"
    assert "devices" in data, "Response should contain 'devices' key"


@settings(max_examples=100, deadline=timedelta(seconds=30))
@given(
    ingress_path=all_ingress_paths_strategy,
)
def test_events_api_with_ingress_path(ingress_path: str):
    """GET /api/events SHALL work correctly with any X-Ingress-Path value.

    **Validates: Requirements 16.9**
    """
    app = _create_test_app()
    client = app.test_client()

    response = client.get(
        "/api/events",
        headers={"X-Ingress-Path": ingress_path},
    )

    assert response.status_code == 200, (
        f"Expected /api/events to return 200 with ingress_path='{ingress_path}', "
        f"got {response.status_code}"
    )

    data = response.get_json()
    assert data is not None, "Response should be valid JSON"
    assert "events" in data, "Response should contain 'events' key"
