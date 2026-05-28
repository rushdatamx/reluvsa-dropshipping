import React from 'react';

export default function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="flex items-start justify-between mb-6 pb-4 border-b border-notion-border">
      <div>
        <h1 className="text-2xl font-bold text-notion-text-primary">{title}</h1>
        {subtitle && <p className="text-sm text-notion-text-secondary mt-1">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}
