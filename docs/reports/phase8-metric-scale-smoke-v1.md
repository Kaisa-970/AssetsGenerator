# Phase 8 Metric Scale v1 Smoke

**Date**: 2026-09-17

An existing TripoSR asset with a Phase 8 convex collision proxy was calibrated by an explicit smoke
measurement: one canonical unit along X was declared to equal 1.8 meters. The CPU workflow completed
without model or GPU execution.

The derived AssetDefinition reports `unit=meter` and `scale_status=metric`. Its visual and collision
artifacts both record `metric_scale_factor=1.8`, preserve canonical `+Z/+X` axes, reload as finite
geometry, and use the same metric AABB extent. This verifies workflow execution and traceability for
one explicit measurement; it does not validate the physical accuracy of the supplied 1.8-meter
claim.

```text
source release: sha256:2f345c786b8c905a85db43e46bc8de5ff3a79fe57e40869b0d6c5e5dc2ae3a22
run: run_e88bb2439fda419cb543a43ac3ccffdb
measurement: sha256:91ed655b18b196dadbb96b2dd8f15bb557bbf1999342705fe5997af06d72dce2
AssetDefinition: sha256:9022fcf68ae8cbaceb7af4d5342f91771d394283ba75d7323d1ac7afe5fc8d73
AssetRelease: sha256:f2e511f1cd636aebde8e208982c7b0a5d892dcece59cce8d6cde0b9a6261d40f
visual mesh: sha256:cbd0bc6de279d2e5dc4623b8fea53ad728aa8a45bba5fc9d4c2db39e9cf80832
collision mesh: sha256:858da75e60f83ebd14124f51e6563d7283ffec55d35aa1cac8f595a42d1904d0
QA: sha256:cbd1b29ed35599aae1101ad52ff28fa8b806111728f75256a0c27a0cb834b29d
```

All generated artifacts remain outside the repository.
