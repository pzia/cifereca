def sync_issues_text(sync):
    detail=sync.get('detail') or {}
    groups=[]
    for label,key in [('Acquisitions','acquisition_errors'),('Sources','provider_errors')]:
        rows=detail.get(key) or []
        if rows:
            lines=[]
            for raw in rows:
                row=raw
                title=row.get('title') or row.get('provider') or 'Opération'
                cause=row.get('error') or row.get('code') or 'Action requise ; consulter Récupérations récentes.'
                lines.append(f"{title} · {row.get('status') or row.get('upstream') or ''}\n{cause}")
            groups.append(label+'\n\n'+'\n\n'.join(lines))
    if detail.get('error'):groups.append(str(detail['error']))
    return '\n\n'.join(groups) or 'Cette ancienne synchronisation ne contient pas le détail des erreurs. Relancez la synchronisation ou consultez Récupérations récentes. Les erreurs de recherche sont distinctes.'
