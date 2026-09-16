"""Windows desktop toast notifications."""
from plyer import notification


def notify(species_common_name, confidence):
    try:
        notification.notify(
            title="Bird heard",
            message=f"{species_common_name} ({confidence:.0%} confidence)",
            app_name="BirdListener",
            timeout=10,
        )
    except Exception as e:
        print(f"[notifier] failed to show notification: {e}")
