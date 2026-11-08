"""Configurer l'alerte Teams par le SDK Prefect : block de notification + automation sur échec terminal.

Chemin : flow en échec → événement Prefect → automation → block Microsoft Teams → Workflows → canal Teams.
L'URL du webhook est un secret : elle est lue dans la variable TEAMS_WEBHOOK_URL (jamais dans Git) et stockée
chiffrée dans le block Prefect. Réexécutable : l'automation existante est mise à jour, pas dupliquée.

Usage (PowerShell) :
  $env:TEAMS_WEBHOOK_URL = "https://...";  uv run --locked python orchestration/setup_alerting.py --test
  uv run --locked python orchestration/setup_alerting.py              # (re)crée l'automation
  uv run --locked python orchestration/setup_alerting.py --disable    # désactive l'automation de test
"""
import argparse
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data' / 'state' / 'alerting.json'
BLOCK_NAME = 'bank-mlops-teams'
AUTOMATION_NAME = 'bank-mlops-echec-pipeline'
FLOW_NAME = 'bank-training-pipeline'

BODY = """Flow : {{ flow.name }} / run {{ flow_run.name }}
Identifiant : {{ flow_run.id }}
État terminal : {{ flow_run.state.name }} ({{ flow_run.state.timestamp }})
Message : {{ flow_run.state.message }}
Lot et run MLflow (tags) : {{ flow_run.tags | join(', ') if flow_run.tags else 'indisponible' }}
Poste / environnement : %s / local
Interface Prefect (sur ce poste uniquement) : {{ flow_run | ui_url }}"""


def save_block(url):
    from prefect.blocks.notifications import MicrosoftTeamsWebhook
    block = MicrosoftTeamsWebhook(url=url)
    block.save(BLOCK_NAME, overwrite=True)
    return MicrosoftTeamsWebhook.load(BLOCK_NAME)


def load_state():
    return json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}


def upsert_automation(block_id):
    from prefect.automations import Automation
    from prefect.events.actions import SendNotification
    from prefect.events.schemas.automations import EventTrigger, Posture

    trigger = EventTrigger(
        expect={'prefect.flow-run.Failed', 'prefect.flow-run.Crashed'},
        match_related={'prefect.resource.role': 'flow', 'prefect.resource.name': FLOW_NAME},
        posture=Posture.Reactive, threshold=1)
    action = SendNotification(block_document_id=block_id,
                              subject='[bank-mlops] Échec du pipeline {{ flow_run.name }}',
                              body=BODY % socket.gethostname())
    state = load_state()
    automation = None
    if state.get('automation_id'):
        try:
            automation = Automation.read(id=state['automation_id'])
        except Exception:  # noqa: BLE001 - supprimée côté serveur
            automation = None
    if automation is None:
        automation = Automation(name=AUTOMATION_NAME, description='Alerte Teams sur échec terminal du pipeline',
                                enabled=True, trigger=trigger, actions=[action]).create()
    else:
        automation.trigger, automation.actions, automation.enabled = trigger, [action], True
        automation.update()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'automation_id': str(automation.id), 'block_id': str(block_id),
                                 'block_name': BLOCK_NAME}, indent=2), encoding='utf-8')
    return automation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help='Envoyer un message « TEST » via le block')
    parser.add_argument('--disable', action='store_true', help="Désactiver l'automation")
    args = parser.parse_args()
    if args.disable:
        from prefect.automations import Automation
        automation = Automation.read(id=load_state()['automation_id'])
        automation.disable()
        print(f'Automation désactivée : {automation.id}')
        return
    url = os.environ.get('TEAMS_WEBHOOK_URL')
    secret_file = ROOT / 'data' / 'state' / 'teams_url.txt'      # hors Git (data/ est ignoré)
    if not url and secret_file.exists():
        url = secret_file.read_text(encoding='utf-8').strip()
    if url:
        block = save_block(url)
    else:
        from prefect.blocks.notifications import MicrosoftTeamsWebhook
        try:
            block = MicrosoftTeamsWebhook.load(BLOCK_NAME)
        except ValueError:
            sys.exit('TEAMS_WEBHOOK_URL absent et aucun block enregistré.')
    if args.test:
        block.notify(f'TEST bank-mlops : notification depuis {socket.gethostname()}',
                     subject='TEST bank-mlops')
        print('Message de test envoyé : vérifier le canal Teams.')
    automation = upsert_automation(block._block_document_id)
    print(json.dumps({'automation_id': str(automation.id), 'block': BLOCK_NAME,
                      'block_document_id': str(block._block_document_id)}, indent=2))


if __name__ == '__main__':
    main()
