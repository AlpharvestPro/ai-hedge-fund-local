"""Render attacker-controlled strings offline without opening market databases."""
import ast
import html
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

class Elements(HTMLParser):
    def __init__(self):
        super().__init__(); self.elements = []
    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

def load_renderer():
    path = Path(__file__).parents[1] / 'scripts/generate_summary.py'
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) or
             (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
              t.id in {'AGENT_SHORT', 'EXCLUDE_FROM_DISPLAY'} for t in node.targets))]
    ns = {'html': html, 'quote': quote, 'Path': Path, 'datetime': datetime}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns['generate_html_summary']

def test_ai_reasoning_and_company_are_text():
    payload = '<img src=x onerror=alert(1)>'
    signals = {'technical_analyst_agent': {'AAPL': {'signal':'neutral', 'confidence':50, 'reasoning':payload}}}
    rendered = load_renderer()(signals, '2026-09-18', 'us', {'AAPL': {'company':payload, 'industry_group':payload}})
    parsed = Elements(); parsed.feed(rendered)
    assert not any(tag == 'img' for tag, _ in parsed.elements)
    assert html.escape(payload) in rendered

def test_ticker_is_never_interpolated_into_javascript():
    ticker = "X');alert(1);//\"><img src=x onerror=alert(1)>"
    signals = {'technical_analyst_agent': {ticker: {'signal':'neutral', 'confidence':50, 'reasoning':'normal'}}}
    rendered = load_renderer()(signals, '2026-09-18', 'us', {})
    parsed = Elements(); parsed.feed(rendered)
    assert not any(tag == 'img' for tag, _ in parsed.elements)
    for _, attrs in parsed.elements:
        for name, value in attrs.items():
            if name.startswith('on'):
                assert 'alert(1)' not in value
    links = [a['href'] for t,a in parsed.elements if t == 'a' and a.get('href','').startswith('https://finviz.com/')]
    assert links and quote(ticker, safe='') in links[0]
