"""Multilingual function-word list for the text-quality heuristic.

Deliberately multilingual. An English-only list would flag every Spanish,
Catalan, French, German, or Italian paper as broken and push it onto the GPU —
the most expensive kind of false positive in the pipeline.

Function words are the right probe because they are short, extremely frequent,
and almost never survive an encoding failure intact: real prose in any of these
languages scores well above the threshold, while mojibake and unmapped glyphs
score near zero.
"""

from __future__ import annotations

ENGLISH = """a an and are as at be been but by for from has have in is it its of
on or that the this to was were which with we our their they he she not been can
could would should may might will these those there here when where how what who
"""

SPANISH = """el la los las un una unos unas de del a en y o que es son ser esta
este estos estas por para con sin sobre entre como mas pero su sus lo se no al
han hay muy tambien cuando donde porque desde hasta cada
"""

CATALAN = """el la els les un una uns unes de del dels a en i o que es son ser
aquest aquesta aquests aquestes per amb sense sobre entre com mes pero seu seus
no al hi ha molt tambe quan on perque des fins cada
"""

FRENCH = """le la les un une des du de a au aux et ou que qui est sont etre cette
ce ces pour avec sans sur entre comme plus mais son ses ne pas dans il elle nous
vous leur ont tres aussi quand ou parce depuis jusqu chaque
"""

GERMAN = """der die das den dem des ein eine einer eines und oder dass ist sind
sein diese dieser dieses fur mit ohne uber zwischen wie mehr aber nicht auch als
auf aus bei nach von vor zu wenn wo weil seit bis jede
"""

ITALIAN = """il lo la i gli le un uno una del della dei delle di a in e o che chi
sono essere questo questa questi queste per con senza su tra come piu ma suo suoi
non anche quando dove perche da fino ogni
"""

STOPWORDS: frozenset[str] = frozenset(
    word
    for block in (ENGLISH, SPANISH, CATALAN, FRENCH, GERMAN, ITALIAN)
    for word in block.split()
)
