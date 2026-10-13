"""Private operator commands; no credentials or customer data in command output."""
import argparse
import json
from pathlib import Path

from backend.billing import Billing
from backend.billing_models import Contract
from backend.billing_store import BillingStore
from backend.cakto import Cakto
from backend.maintenance import configured_store
from backend.providers import ProviderUnavailable


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['import','connection','reconcile','bind'])
    parser.add_argument('--file');parser.add_argument('--installment');parser.add_argument('--order')
    args=parser.parse_args();private=configured_store();store=BillingStore(private)
    if args.command=='import':
        if not args.file:raise SystemExit('Informe --file com o contrato privado já assinado e desembolsado.')
        try:
            path=Path(args.file)
            if path.stat().st_size>32768:raise ValueError()
            model=Contract.model_validate_json(path.read_bytes())
            identity=store.import_contract(model)
        except Exception:raise SystemExit('Contrato inválido ou divergente. Nenhuma cobrança foi criada.') from None
        print('Contrato registrado: '+identity)
    elif args.command=='connection':
        try:result=Cakto().check_connection()
        except ProviderUnavailable:raise SystemExit('Não foi possível confirmar a autenticação e os escopos da Cakto.') from None
        print(json.dumps(result))
    else:
        if not args.installment:raise SystemExit('Informe --installment com o protocolo interno da parcela.')
        # These commands only query an existing order; they never create a charge.
        class Context:
            enabled=True;clock=staticmethod(private.clock)
        context=Context();context.private=private;service=Billing(context);service.store=store
        contract,item,_,attempt=store.installment(args.installment)
        if not attempt:raise SystemExit('A parcela não possui tentativa de pagamento.')
        if args.command=='bind':
            if not args.order:raise SystemExit('Informe --order com o pedido existente na Cakto.')
            state,bound,data=store.attempt(attempt)
            if bound and bound!=args.order:raise SystemExit('A parcela já está vinculada a outro pedido.')
            try:
                order=service.provider.order(args.order)
                service.validate_order(order,args.order,item,contract['customer'],attempt)
            except ProviderUnavailable:raise SystemExit('Pedido, CPF, valor ou referência não correspondem à parcela.') from None
            store.save_attempt(attempt,'review',data,args.order)
        try:service.reconcile(args.installment,True)
        except ProviderUnavailable:raise SystemExit('Não foi possível confirmar o pagamento. Nenhum estado foi presumido.') from None
        private.audit('operator','installment_checked',args.installment)
        print('Estado conferido: '+store.installment(args.installment)[2])


if __name__=='__main__':main()
