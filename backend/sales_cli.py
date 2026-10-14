"""Read-only provider preflight and recovery of existing independent purchases."""
import argparse
import base64
import json
import os
import sys
import uuid
from pathlib import Path

from backend.app import Store
from backend.private_store import PrivateStore
from backend.sales import Sales, SalesCakto, PRODUCT_NAME, PRICE, TOTAL, OFFER_ID
from backend.security import Security
from backend.cakto import cents

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','reconcile','bind'])
    parser.add_argument('--purchase');parser.add_argument('--order')
    args=parser.parse_args();provider=SalesCakto()
    try:
        if args.command=='check':
            connection=provider.check_connection();offer=provider.offer(os.getenv('CAKTO_SALES_OFFER_ID',OFFER_ID))
            valid=(offer.get('id')==os.getenv('CAKTO_SALES_OFFER_ID',OFFER_ID) and offer.get('name')==PRODUCT_NAME
                   and offer.get('status')=='active' and offer.get('type')=='unique'
                   and offer.get('currency')=='BRL' and cents(offer.get('price'))==PRICE)
            print(json.dumps({'authenticated':connection['authenticated'],'sales_enabled':provider.can_charge,'payment_scopes_ready':connection['can_charge'],
                              'offer_matches':valid,'product_name':PRODUCT_NAME,'amount_cents':TOTAL}))
            if not valid or not connection['can_charge']:sys.exit(1)
            return
        identity=str(uuid.UUID(args.purchase));key=base64.b64decode(os.environ['DATA_ENCRYPTION_KEY'],validate=True)
        store=Store(Path(os.environ['APPLICATIONS_DB']),key);security=Security(os.environ['PUBLIC_ORIGIN'],False)
        security.private=PrivateStore(store);sales=Sales(security,provider);sales.open()
        with store.connect() as db:found=db.execute('SELECT session_id FROM product_sales WHERE id=?',(identity,)).fetchone()
        if not found:raise ValueError('Unknown purchase')
        with sales.private.locked('sale:'+found[0]):
            row=sales.row(found[0])
            if args.command=='bind':
                order=str(uuid.UUID(args.order))
                if row['order_id'] and row['order_id']!=order:raise ValueError('Existing order')
                row['order_id']=order
            sales.reconcile(row,True)
            print(json.dumps({'purchase_id':identity,'status':row['status']}))
    except Exception:
        print('Não foi possível conferir a compra. Revise a configuração e os identificadores; nenhum Pix foi criado.',file=sys.stderr)
        sys.exit(1)

if __name__=='__main__':main()
