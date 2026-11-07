"""The 10 faults of the v0.0 test, written down before any trial ran.

`relevant` decides which alerts count as catching the fault: a list of (alertname regex, {label: regex})
clauses, and an alert is relevant if any clause matches it. Changing a list after results exist needs
a commit that says why, and earlier results keep the list they were scored with.

`harm` says what must go wrong for the trial to count:
- probe "web": /work's success rate falls below 95% over 60 seconds, or its p95 latency passes p95_ms
- probe "big": the same for /work?big=1, which fetches 1 MB across nodes
- probe "api": a server-side dry run of creating a pod fails
- gone: the named monitoring component has no pods left
"""

SHOP_WORKLOAD = (r"Kube(Pod|Deployment|Container|ReplicaSet).*", {"namespace": "shop"})
ANYTHING_IN_SHOP = (r".*", {"namespace": "shop"})
DNS = [(r"CoreDNS.*|KubeDNS.*", {}), (r".*", {"job": "coredns|kube-dns"})]

FAULTS = {
    "crashloop": {
        "number": 1,
        "name": "App keeps crashing",
        "harm": {"probe": "web"},
        "relevant": [SHOP_WORKLOAD],
    },
    "cpu-throttle": {
        "number": 5,
        "name": "CPU throttled",
        "harm": {"probe": "web", "p95_ms": 1500},
        "relevant": [(r"CPUThrottlingHigh", {"namespace": "shop"}), SHOP_WORKLOAD],
    },
    "dns-nxdomain": {
        "number": 8,
        "name": "Upstream DNS returns NXDOMAIN",
        "harm": {"probe": "web"},
        "relevant": DNS,
    },
    "dns-slow": {
        "number": 9,
        "name": "DNS very slow",
        "harm": {"probe": "web", "p95_ms": 1500},
        "relevant": DNS,
    },
    "packet-loss": {
        "number": 10,
        "name": "Packet loss between pods",
        "harm": {"probe": "web", "p95_ms": 1500},
        "relevant": [ANYTHING_IN_SHOP, (r"NodeNetwork.*", {})],
    },
    "mtu-blackhole": {
        "number": 11,
        "name": "MTU black hole",
        "harm": {"probe": "big"},
        "relevant": [ANYTHING_IN_SHOP, (r"NodeNetwork.*", {})],
    },
    "netpol-deny": {
        "number": 14,
        "name": "NetworkPolicy blocks the app",
        "harm": {"probe": "web"},
        "relevant": [ANYTHING_IN_SHOP],
    },
    "webhook-down": {
        "number": 25,
        "name": "Admission webhook down",
        "harm": {"probe": "api"},
        "relevant": [(r"KubeAPI.*|KubeClientErrors", {}), ANYTHING_IN_SHOP],
    },
    "alertmanager-down": {
        "number": 32,
        "name": "Alertmanager down, then the app crashes",
        "harm": {"probe": "web", "gone": "alertmanager"},
        "relevant": [
            SHOP_WORKLOAD,
            (r"Alertmanager.*|PrometheusNotConnectedToAlertmanagers|PrometheusErrorSendingAlerts.*", {}),
            (r"TargetDown", {"job": ".*alertmanager.*"}),
        ],
    },
    "prometheus-down": {
        "number": 33,
        "name": "Prometheus down",
        "harm": {"gone": "prometheus"},
        "relevant": [(r"Prometheus.*", {}), (r"TargetDown", {"job": ".*prometheus.*"})],
    },
}

# Never evidence of anything on their own.
NEVER_RELEVANT = {"Watchdog", "InfoInhibitor"}
