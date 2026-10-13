"""Configure only public endpoint paths on the copy deployed to the VPS."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--vps',action='store_true')
    group.add_argument('--static',action='store_true')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1]/'src/business-config.json'
    config = json.loads(path.read_text())
    for name,value in {'applicationsApiUrl':'/api/applications','securityApiUrl':'/api/security',
                       'pfApplicationsApiUrl':'/api/pf/applications','paymentApiUrl':'/api/installments'}.items():
        config[name] = value if args.vps else None
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    print('Rotas públicas configuradas. Reconstrua a página; nenhuma credencial foi gravada no site.')


if __name__ == '__main__':
    main()
