class EntitlementDenied(Exception):
    def __init__(self, subscriber_id: str, entitlement_id: str):
        self.subscriber_id = subscriber_id
        self.entitlement_id = entitlement_id
        super().__init__(
            f"Entitlement '{entitlement_id}' denied for subscriber '{subscriber_id}'"
        )
