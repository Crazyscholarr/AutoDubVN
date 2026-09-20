"""Server adapter for the shared ASR review authority; job flags are projections."""
import os
from pathlib import Path
from ..asr.nonspeech import (bind_source,effective_review,load_latest_review,source_context,_read_json)
from .helpers import _path_under


def review_for_job(job, project=None, base_dir=None):
    from . import helpers
    from .projects import _active_media_span,_run_stem_for_project
    base=os.path.abspath(os.path.join(base_dir or helpers.HERE,'output'))
    root=str(job.get('review_dir') or '')
    if not (root and os.path.isdir(root) and Path(root).name.startswith('caption-review-')
            and _path_under(base,root)):
        root=''
    anchor=None
    context={}
    if project and project.get('video') and os.path.isfile(project['video']):
        span=_active_media_span(project)
        stem,_=_run_stem_for_project(project,span)
        anchor=Path(base)/stem/'_tmp'
        context=bind_source(anchor,project['video'],
                            {'start':round(span['start'],3),'end':round(span['end'],3)} if span['enabled'] else {})
    elif root:
        anchor=Path(root).parent
        context=source_context(anchor)
    if anchor is None:
        # A legacy job with no readable artifact must remain reviewable, not bypass the gate.
        from ..asr.review import review_state
        rows=[dict(r,withheld=True) for r in job.get('review_gaps',[]) if isinstance(r,dict)] or ([dict(reason='review_artifact_missing',start=0,end=.001,withheld=True)]
              if job.get('result_status')=='REVIEW_REQUIRED' else [])
        return dict(review_state(rows),review_dir='',anchor=None)
    saved=load_latest_review(anchor)
    fallback=saved.get('rows')
    if not isinstance(fallback,list):
        fallback=[dict(r,withheld=True) for r in job.get('review_gaps',[]) if isinstance(r,dict)]
        if not fallback and job.get('result_status')=='REVIEW_REQUIRED':
            fallback=[dict(reason='review_artifact_missing',start=0,end=.001,withheld=True)]
    if context.get('require_asr'):
        root=''
        rows=[dict(reason='source_changed',start=0,end=.001,withheld=True)]
    elif isinstance(saved.get('rows'),list):
        # The durable snapshot is authoritative, including an empty result.
        # A stale job pointer may reference an empty directory left by a crash.
        rows=saved['rows']
        candidate=str(saved.get('review_dir') or '')
        root=(candidate if candidate and os.path.isdir(candidate)
              and Path(candidate).name.startswith('caption-review-')
              and _path_under(str(anchor),candidate) else '')
    else:
        if not root:
            candidate=str(saved.get('review_dir') or '')
            if candidate and os.path.isdir(candidate) and _path_under(str(anchor),candidate):
                root=candidate
        if root:
            rows=_read_json(Path(root)/'unresolved.json',None)
            if isinstance(rows,list):
                rows=[dict(r,withheld=True) for r in rows if isinstance(r,dict)]
            # Preserve raw diagnostics, including the resolved ones, when available.
            raw=_read_json(Path(root)/'review.json',None)
            if isinstance(raw,list):
                rows=raw
            if not isinstance(rows,list):
                rows=fallback
        else:
            rows=fallback
        if rows is None:
            rows=([dict(reason='review_artifact_missing',start=0,end=.001,withheld=True)]
                  if job.get('result_status')=='REVIEW_REQUIRED' else [])
    state=effective_review(anchor,rows)
    prepared=bool(saved.get('prepared') and root and
                  os.path.normcase(os.path.abspath(root))==
                  os.path.normcase(os.path.abspath(saved.get('review_dir') or '.')))
    return dict(state,review_dir=root,anchor=str(anchor),prepared=prepared)


def project_review_to_job(job,state):
    """Keep review UI available after restart, even when all decisions are complete."""
    if state['review_dir']:
        job['review_dir']=state['review_dir']
        job['working_source']=str(Path(state['review_dir'])/'source.srt')
    job['review_gaps']=[dict(start=r['start'],end=r['end'],reason=r['reason'],issue_id=r['issue_id'])
                        for r in state['items'] if r['requires_review']]
    if state['effective_blockers'] or (state['review_dir'] and not state.get('prepared')):
        job['result_status']='REVIEW_REQUIRED'
        job['status']='cần kiểm tra' if state['effective_blockers'] else 'chờ'
    job['review_unresolved']=state['unresolved']
