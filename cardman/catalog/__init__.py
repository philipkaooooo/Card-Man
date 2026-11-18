"""The master card list ("printing registry"): every buyable version of every card in both games, merged from several
sources with exact rules, reprints linked to their originals, and a completeness check that fails loudly.

  tcgcsv.py     TCGplayer's catalog and daily prices via TCGCSV (both games), cached locally
  registry.py   build: our catalogs + TCGplayer, matched by set code + number + variant; reprint_of; missing list
  export.py     data/exports/catalog_{game}.csv / .xlsx for browsing
"""
