from inventory_mirror import build_servers, parse_args


def test_parse_args_defaults():
    args = parse_args(["--token-file", "tok"])
    assert args.push_port == 7892 and args.read_port == 7893
    assert args.allowlist == []


def test_parse_args_allowlist_split():
    args = parse_args(["--token-file", "tok", "--allowlist", "a@x.com,b@y.com"])
    assert args.allowlist == ["a@x.com", "b@y.com"]


def test_parse_args_allowlist_strips_surrounding_quotes():
    # schtasks (Windows) can pass the value with literal surrounding quotes;
    # they must be stripped or the entry never matches Tailscale-User-Login.
    args = parse_args(["--token-file", "tok", "--allowlist", '"a@x.com"'])
    assert args.allowlist == ["a@x.com"]
    args = parse_args(["--token-file", "tok", "--allowlist", '"a@x.com","b@y.com"'])
    assert args.allowlist == ["a@x.com", "b@y.com"]
    # An empty quoted value yields an empty allowlist (deny-all-tailnet).
    assert parse_args(["--token-file", "tok", "--allowlist", '""']).allowlist == []


def test_build_servers_reads_token_and_loads_snapshot(tmp_path):
    tok = tmp_path / "tok"
    tok.write_text("s3cr3t", encoding="utf-8")
    snap = tmp_path / "snap.json"
    snap.write_text('{"inventory":[{"lcsc":"X"}]}', encoding="utf-8")
    args = parse_args(["--token-file", str(tok), "--snapshot-file", str(snap),
                       "--push-port", "0", "--read-port", "0"])
    push, read, store = build_servers(args)
    try:
        assert push.token == "s3cr3t"
        assert store.get()["inventory"] == [{"lcsc": "X"}]
    finally:
        push.server_close()
        read.server_close()
