import * as React from 'react';

declare global {
  namespace JSX {
    interface IntrinsicElements {
      'vitallens-monitor': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}

export {};
