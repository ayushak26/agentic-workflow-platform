import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const { login } = vi.hoisted(() => ({ login: vi.fn() }));

vi.mock('../../api/client', () => ({ login }));

import { LoginPage } from './LoginPage';

describe('LoginPage', () => {
  it('exposes accessible credential fields with browser autocomplete metadata', () => {
    render(<LoginPage onLogin={() => undefined} />);

    expect(screen.getByLabelText('Username')).toHaveAttribute('autocomplete', 'username');
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password');
  });

  it('submits credentials and reports the authenticated username', async () => {
    const user = userEvent.setup();
    const onLogin = vi.fn();
    login.mockResolvedValueOnce({ username: 'ayush' });
    render(<LoginPage onLogin={onLogin} />);

    await user.type(screen.getByLabelText('Username'), 'ayush');
    await user.type(screen.getByLabelText('Password'), 'dev123');
    await user.click(screen.getByRole('button', { name: /^Sign in$/ }));

    expect(login).toHaveBeenCalledWith('ayush', 'dev123');
    expect(onLogin).toHaveBeenCalledWith('ayush');
  });
});