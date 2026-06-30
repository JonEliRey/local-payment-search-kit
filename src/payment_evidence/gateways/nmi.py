from __future__ import annotations

from .base import GatewayAdapterInfo, GatewayCapability

NMI_ADAPTER_INFO = GatewayAdapterInfo(
    name="nmi",
    display_name="NMI / MBCard Query API",
    capabilities=[
        GatewayCapability("transaction_lookup", True, "Lookup by transaction_id"),
        GatewayCapability("order_lookup", True, "Lookup by order_id with optional bounded dates"),
        GatewayCapability("amount_search", True, "Search by amount inside bounded UTC window"),
        GatewayCapability("same_customer_history", False, "Planned next capability"),
        GatewayCapability("dashboard_generation", False, "Core capability planned outside adapter"),
    ],
)
