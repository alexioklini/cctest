#!/bin/sh
# apply_beta_drift_patches.sh — Beta-Drift-Patches für die CoreAIKit-Dependencies
# (Muster analog zu den MemPalace-venv-Patches: Upstream ist gegen eine ältere
# macOS-27-Beta gebaut; das Beta-5-SDK hat zwei API-Formen geändert. Nach jedem
# `swift package update`/Pin-Wechsel NEU anwenden — und den Diff neu reviewen.)
#
# Nutzung (auf dem Build-Host, im fm-agent-Package-Verzeichnis):
#   swift package edit coreai-models
#   swift package edit coreai-kit
#   ./apply_beta_drift_patches.sh
#
# Patch 1: LanguageModelCapabilities(capabilities:) → LanguageModelCapabilities(_:)
#          (Beta-5-SDK: init ist ungelabelt)
# Patch 2: Metadata-Dictionaries [String: any Sendable & Codable & Equatable]
#          → [String: any ConvertibleToGeneratedContent]
#          (Beta-5-SDK: updateMetadata verlangt ConvertibleToGeneratedContent)
set -e
cd "$(dirname "$0")"
[ -d Packages ] || { echo "FEHLER: Packages/ fehlt — erst 'swift package edit coreai-models' + 'swift package edit coreai-kit'"; exit 1; }

n=0
for f in $(grep -rl "LanguageModelCapabilities(capabilities:" Packages/ 2>/dev/null); do
    sed -i "" "s/LanguageModelCapabilities(capabilities:/LanguageModelCapabilities(/g" "$f"
    echo "Patch 1: $f"; n=$((n+1))
done
for f in $(grep -rl "\[String: any Sendable & Codable & Equatable\]" Packages/ 2>/dev/null); do
    sed -i "" "s/\[String: any Sendable \& Codable \& Equatable\]/[String: any ConvertibleToGeneratedContent]/g" "$f"
    echo "Patch 2: $f"; n=$((n+1))
done
echo "fertig — $n Datei(en) gepatcht (0 = bereits angewendet oder Upstream gefixt)"
