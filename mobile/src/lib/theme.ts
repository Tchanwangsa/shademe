import { useColorScheme } from 'react-native';

/** Hex values for the places Tailwind classes cannot reach: icon `color` props, the
 * MapLibre style layers, and the bottom sheet's own chrome. Kept in step with
 * tailwind.config.js by hand -- there are few enough of them that a build-time bridge
 * would cost more than it saves.
 */
const LIGHT = {
  scheme: 'light' as const,
  ink: '#12140F',
  inkMuted: '#5C5F55',
  inkSoft: '#8A8D82',
  paper: '#FFFFFF',
  sunken: '#F4F4F1',
  line: '#E4E4DF',
  sun: '#C97A16',
  shade: '#1D9E75',
  indoor: '#3B82C4',
  routeCool: '#1D9E75',
  routeDirect: '#8A8D82',
  routeIdle: '#B9BCB2',
};

const DARK: typeof LIGHT = {
  scheme: 'dark' as unknown as 'light',
  ink: '#F4F4F1',
  inkMuted: '#A2A69A',
  inkSoft: '#8A8D82',
  paper: '#1E211C',
  sunken: '#141613',
  line: '#2C2F29',
  sun: '#E9A44B',
  shade: '#5DCAA5',
  indoor: '#7FB2E0',
  routeCool: '#5DCAA5',
  routeDirect: '#8A8D82',
  routeIdle: '#4A4E45',
};

export function useTheme() {
  const scheme = useColorScheme();
  const isDark = scheme === 'dark';
  return { ...(isDark ? DARK : LIGHT), isDark };
}

export type Theme = ReturnType<typeof useTheme>;
