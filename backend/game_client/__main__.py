"""CLI: verified preflight or ticket-authorized, read-only source investigation.

No daemon, password login, authentication bypass, or production source adapter.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from .client import GameClient, ProtocolProfile, validate_public_uid
from .diagnostics import FieldReport, game_snapshot, private_json
from .errors import GameClientError, RpcError
from .history import collect_history
from .session import FileTicketProvider, LoadedTicketProvider


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=[item.value for item in ProtocolProfile], default="tls-rpc")
    parser.add_argument("--named-legacy-hello", action="store_true")
    parser.add_argument("--preflight", action="store_true", help="Verified TLS plus a no-ticket code-23 read; expected RPC 401")
    parser.add_argument("--transport-only", action="store_true", help="Only a verified TLS connection; no RPC/authentication")
    parser.add_argument("--ticket-file", help="Owner-only file containing a legitimate game session ticket")
    parser.add_argument("--uid", help="Public game UID; resolved to internal source ID after authentication")
    parser.add_argument("--max-matches", type=int, default=50)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--schema-report", help="Private bounded JSON report, field names/types only")
    parser.add_argument("--snapshot", help="Optional private bounded public-game response JSON; never includes handshake")
    parsed = parser.parse_args(argv)
    if parsed.ticket_file and (parsed.preflight or parsed.transport_only):
        parser.error("preflight/transport-only must not receive a ticket")
    if parsed.preflight and parsed.transport_only:
        parser.error("choose preflight or transport-only")
    if parsed.ticket_file and not parsed.uid:
        parser.error("--uid is required with --ticket-file")
    if (parsed.schema_report or parsed.snapshot or parsed.uid) and not parsed.ticket_file:
        parser.error("data requests require --ticket-file")
    if parsed.named_legacy_hello and parsed.profile != "legacy-aes":
        parser.error("--named-legacy-hello requires --profile legacy-aes")
    if parsed.transport_only and parsed.profile != "tls-rpc":
        parser.error("--transport-only requires --profile tls-rpc; legacy initialization sends hello")
    if not 1 <= parsed.max_matches <= 50 or not 1 <= parsed.page_size <= 50 or not 1 <= parsed.max_pages <= 100:
        parser.error("history bounds exceeded")
    return parsed


async def run(args):
    report = {"protocol_profile": args.profile, "tls_verified": False,
              "game_authenticated": False, "player_data_received": False,
              "history_data_received": False, "uid_method_verified": False,
              "history_method_verified": False, "source_integration_ready": False}
    try:
        provider = None
        if args.ticket_file:
            validate_public_uid(args.uid)
            provider = LoadedTicketProvider(await FileTicketProvider(args.ticket_file).get_ticket())
        async with GameClient(profile=ProtocolProfile(args.profile), named_legacy_hello=args.named_legacy_hello) as client:
            report["tls_verified"] = True
            report["legacy_hello_verified"] = client.hello_validated
            if not args.ticket_file:
                if not args.transport_only:
                    report.update(await client.probe_authorization_boundary())
                    return report, 0 if report.get("rpc_boundary_verified") else 2
                return report, 0
            await client.authenticate(provider)
            report["game_authenticated"] = True
            player = await client.resolve_uid(args.uid)
            report["player_data_received"] = True
            report["uid_method_verified"] = True
            batch = await collect_history(client.history_page, player, max_matches=args.max_matches,
                                          page_size=args.page_size, max_pages=args.max_pages)
            report["history_method_verified"] = True
            report["history_data_received"] = bool(batch.matches)
            report["history"] = batch.summary()
            if args.schema_report:
                schema = FieldReport()
                schema.observe(player.profile, "profile")
                for match in batch.matches:
                    schema.observe(match, "match")
                private_json(args.schema_report, schema.summary())
                report["schema_report_written"] = True
            if args.snapshot:
                private_json(args.snapshot, game_snapshot(player, batch))
                report["private_snapshot_written"] = True
            # Actual mode/statistic keys remain unverified: raw transport success
            # must never make the production app advertise a ready data source.
            return report, 0
    except GameClientError as error:
        report["error"] = error.code
        if isinstance(error, RpcError):
            report["rpc_error_code"] = error.remote_code
            if error.remote_code in (401, 2003):
                report["game_authenticated"] = False
        return report, 1
    except (OSError, ValueError):
        report["error"] = "invalid_local_configuration"
        return report, 1


def main(argv=None):
    args = arguments(argv)
    try:
        report, code = asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    # Only deliberately constructed status fields reach stdout. Never stringify
    # arbitrary exceptions, protobuf responses, provider instances or argparse.
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
