"""The Prometheus text exposition format."""

from src.scrape import instance_of, parse

TEXT = """# HELP http_requests_total The total number of HTTP requests.
# TYPE http_requests_total counter
http_requests_total{method="post",code="200"} 1027 1395066363000
http_requests_total{method="post",code="400"}    3 1395066363000
msdos_file_access_time_seconds{path="C:\\\\DIR\\\\FILE.TXT",error="Cannot find file:\\n\\"FILE.TXT\\""} 1.458255915e9
# TYPE rpc_duration_seconds summary
rpc_duration_seconds{quantile="0.5"} 4773
rpc_duration_seconds_sum 1.7560473e+07
rpc_duration_seconds_count 2693
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.05"} 24054
http_request_duration_seconds_bucket{le="+Inf"} 144320
http_request_duration_seconds_sum 53423
http_request_duration_seconds_count 144320
# TYPE temperature gauge
temperature NaN
temperature{room="attic"} +Inf
temperature{room="kitchen"} -3.5
metric_without_timestamp_and_labels 12.47
broken{ 1
"""


def test_parse():
    samples, errors = parse(TEXT)
    assert errors == 1
    by_name = {}
    for s in samples:
        by_name.setdefault(s.name, []).append(s)
    requests = by_name["http_requests_total"]
    assert [s.value for s in requests] == [1027, 3]
    assert requests[0].labels == {"method": "post", "code": "200"}
    assert requests[0].kind == "counter" and requests[0].ts.year == 2014
    msdos = by_name["msdos_file_access_time_seconds"][0]
    assert msdos.labels == {"path": "C:\\DIR\\FILE.TXT", "error": 'Cannot find file:\n"FILE.TXT"'}
    assert msdos.kind == "untyped"
    assert by_name["rpc_duration_seconds"][0].labels == {"quantile": "0.5"}
    assert by_name["rpc_duration_seconds"][0].kind == "summary"
    assert by_name["rpc_duration_seconds_count"][0].kind == "counter"
    assert "http_request_duration_seconds_bucket" not in by_name
    assert by_name["http_request_duration_seconds_count"][0].value == 144320
    # NaN and the infinities are skipped
    assert [(s.labels, s.value) for s in by_name["temperature"]] == [({"room": "kitchen"}, -3.5)]
    assert by_name["metric_without_timestamp_and_labels"][0].ts is None


def test_instance():
    assert instance_of("http://nas:9100/metrics") == "nas:9100"
    assert instance_of("https://router/metrics") == "router:443"
    assert instance_of("http://10.0.0.1/metrics") == "10.0.0.1:80"
