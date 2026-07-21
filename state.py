"""Persisted stock state.

The whole point of the bot is detecting the *transition* from out-of-stock
to in-stock. That requires remembering what we saw last time, surviving
restarts -- and on GitHub Actions, surviving the machine being destroyed
after every run (the state file gets committed back to the repo).

Because of that, the serialised form deliberately contains NO "last checked"
timestamp. If it did, the file would differ after every single run and the
workflow would push a commit every few minutes. As written, the file changes
only when stock actually changes.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Iterable

from stores.common import Item

log = logging.getLogger(__name__)


class StockState:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, dict] = {}
        self.seeded: bool = False
        self.changed: bool = False
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            log.info("No state file at %s -- first run will seed, not alert", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.data = payload.get("items", {})
            self.seeded = bool(payload.get("seeded"))
            log.info("Loaded state for %d items", len(self.data))
        except (OSError, ValueError) as exc:
            log.error("Could not read state file (%s) -- starting fresh", exc)
            self.data = {}

    def _serialise(self) -> str:
        payload = {"seeded": self.seeded, "items": self.data}
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    def save(self) -> bool:
        """Write state, but only if the contents actually differ.

        Returns True if the file was written. Skipping no-op writes is what
        keeps the GitHub Actions workflow from committing every few minutes.
        """
        content = self._serialise()

        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    if handle.read() == content:
                        log.debug("State unchanged -- not rewriting %s", self.path)
                        return False
            except OSError:
                pass

        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)

        # Atomic write: a crash mid-save must not corrupt the state file.
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

        self.changed = True
        return True

    # ------------------------------------------------------------------
    def diff(self, items: Iterable[Item]) -> list[Item]:
        """Update stored state and return items that just came back in stock.

        On the very first run we record everything and alert on nothing --
        otherwise you'd get pinged for every item that happens to be in
        stock right now, which isn't a restock.
        """
        items = list(items)
        restocked: list[Item] = []
        first_run = not self.seeded
        now = int(time.time())

        for item in items:
            previous = self.data.get(item.key, {})
            was_available = previous.get("available")

            just_restocked = (
                not first_run and was_available is False and item.available
            )
            if just_restocked:
                restocked.append(item)

            record = {
                "available": item.available,
                "store": item.store,
                "name": item.name,
                "url": item.url,
                "detection": item.detection,
            }
            if item.variant:
                record["variant"] = item.variant
            if item.price:
                record["price"] = item.price

            # Only touches the file when a restock genuinely happens.
            last_restock = now if just_restocked else previous.get("last_restock")
            if last_restock:
                record["last_restock"] = last_restock

            self.data[item.key] = record

        if first_run:
            self.seeded = True
            in_stock = sum(1 for i in items if i.available)
            log.info(
                "Seeded state with %d items (%d currently in stock). "
                "Alerts start from the next run.",
                len(self.data),
                in_stock,
            )

        wrote = self.save()
        log.info("State file %s", "updated" if wrote else "unchanged")
        return restocked

    # ------------------------------------------------------------------
    def in_stock_summary(self) -> list[dict]:
        return [record for record in self.data.values() if record.get("available")]
