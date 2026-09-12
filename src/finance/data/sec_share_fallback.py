"""Experimental, conservative fallback; absence of class conflicts is not proof
of single-class economic scope. Run an impact audit before production adoption.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def equity_component_share_candidates(supported, presentation, canonical):
    """Return current-period EQ common-stock facts only where no eligible total
    exists. Input is the mapped, submission-joined, supported-form frame.
    Never strip dimensions off the returned evidence.
    """
    required = {'adsh', 'tag', 'version', 'segments', 'coreg', 'qtrs',
                'uom', 'value', 'ddate_date', 'period_date'}
    if presentation is None or not required <= set(supported.columns):
        return supported.iloc[0:0].copy()
    if not {'adsh', 'tag', 'version', 'stmt'} <= set(presentation.columns):
        return supported.iloc[0:0].copy()
    eq_keys = set(presentation.loc[presentation['stmt'].eq('EQ'),
                                   ['adsh', 'tag', 'version']].itertuples(index=False, name=None))
    shares = supported.loc[supported['concept'].eq('shares_outstanding')].copy()
    existing = set(canonical.loc[canonical['concept'].eq('shares_outstanding'), 'adsh'])
    output = []
    for adsh, group in shares.groupby('adsh', sort=False):
        if adsh in existing:
            continue
        # Inspect all share contexts in the filing, including comparative periods.
        # Extra axes, class members, unknown dimensions, or co-registrants block
        # the entire fallback. Do not sum classes or select one by magnitude.
        segments = group['segments'].fillna('').astype(str).str.strip()
        coreg = group['coreg'].fillna('').astype(str).str.strip()
        if (~segments.isin(['', 'EquityComponents=CommonStock;'])).any() or coreg.ne('').any():
            continue
        current = group.loc[group['ddate_date'].eq(group['period_date'])
                            & pd.to_numeric(group['qtrs'], errors='coerce').eq(0)]
        if current.empty:
            continue
        values = pd.to_numeric(current['value'], errors='coerce')
        # Even a conflicting direct fact that failed placement blocks fallback.
        if (current['uom'].ne('shares').any() or not np.isfinite(values).all()
                or values.le(0).any() or values.nunique() != 1):
            continue
        candidates = current.loc[
            current['tag'].eq('CommonStockSharesOutstanding')
            & current['segments'].fillna('').astype(str).str.strip().eq('EquityComponents=CommonStock;')
        ].copy()
        candidates = candidates.loc[
            [(r.adsh, r.tag, r.version) in eq_keys for r in candidates.itertuples()]
        ]
        if not candidates.empty:
            candidates['share_selection_rule'] = 'experimental_eq_common_stock_v1'
            output.append(candidates)
    if not output:
        return supported.iloc[0:0].copy()
    return pd.concat(output, ignore_index=True)
