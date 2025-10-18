"""Ansible filter for detecting published port conflicts."""

from __future__ import annotations

from typing import List, Mapping, Sequence


class FilterModule:
    """Expose custom filters to Ansible."""

    def filters(self) -> Mapping[str, object]:
        return {"calculate_port_conflicts": self.calculate_port_conflicts}

    @staticmethod
    def calculate_port_conflicts(
        service_ports: Sequence[Mapping[str, object]] | None,
        target_port: int | str,
    ) -> List[Mapping[str, object]]:
        """Return the subset of published ports that collide with ``target_port``.

        Args:
            service_ports: All published port mappings for the inventory.
            target_port: The host port under evaluation.

        Returns:
            A list of port mappings whose ``published`` value equals ``target_port``.
        """

        if service_ports is None:
            return []

        try:
            target = int(target_port)
        except (TypeError, ValueError):
            return []

        conflicts: List[Mapping[str, object]] = []
        for port in service_ports:
            if not isinstance(port, Mapping):
                continue
            published = port.get("published")
            try:
                published_port = int(published)
            except (TypeError, ValueError):
                continue
            if published_port == target:
                conflicts.append(port)
        return conflicts
