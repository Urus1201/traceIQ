import { render } from '@testing-library/react';
import App from './App';

test('renders healthcheck', () => {
  const { getByText } = render(<App />);
  expect(getByText('Viewer UI is running')).toBeInTheDocument();
});
