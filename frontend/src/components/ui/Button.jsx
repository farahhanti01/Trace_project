import React from 'react';
import { cn } from '../../lib/utils';

export function Button({ children, className = '', variant = 'default', size = 'md', ...props }) {
  const base = 'inline-flex items-center justify-center rounded-md font-medium';
  const variants = {
    default: 'bg-white border border-gray-200 text-sm',
    outline: 'bg-white border border-gray-200 text-sm',
    icon: 'bg-white border border-gray-200 p-2'
  };
  const sizes = {
    sm: 'text-sm h-8 px-3',
    md: 'text-sm h-9 px-4',
    icon: 'p-2'
  };
  return (
    <button className={cn(base, variants[variant] || variants.default, sizes[size] || sizes.md, className)} {...props}>
      {children}
    </button>
  );
}

export default Button;
