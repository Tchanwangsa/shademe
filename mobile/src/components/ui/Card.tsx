import { View, type ViewProps } from 'react-native';
import { cn } from '../../lib/cn';

export function Card({ className, ...props }: ViewProps) {
  return (
    <View
      className={cn(
        'rounded-2xl border border-line bg-paper dark:border-line-dark dark:bg-night-raised',
        className,
      )}
      {...props}
    />
  );
}

export function Surface({ className, ...props }: ViewProps) {
  return (
    <View
      className={cn('rounded-xl bg-paper-sunken dark:bg-night', className)}
      {...props}
    />
  );
}
