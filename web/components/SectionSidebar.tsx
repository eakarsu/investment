import { useState } from 'react';
import './SectionSidebar.css';

type Item = { href: string; label: string };

export default function SectionSidebar({ title, items }: { title: string; items: Item[] }) {
  const [query, setQuery] = useState('');
  const visible = items.filter(item => item.label.toLowerCase().includes(query.toLowerCase().trim()));
  return <aside className="codex-section-side" aria-label="Application navigation">
    <div className="codex-section-brand"><strong>{title}</strong><span>Workspace</span></div>
    <label htmlFor="codex-section-search">Find a section</label>
    <input id="codex-section-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search navigation" />
    <nav aria-label="Sections">
      {visible.map(item => <a key={item.href} href={item.href}>{item.label}</a>)}
      {visible.length === 0 && <p>No matching sections</p>}
    </nav>
  </aside>;
}
