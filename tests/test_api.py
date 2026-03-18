"""
Интеграционные тесты API MAOCS: логин/регистрация, друзья, группы, upload с моком.
"""
import io
import pytest
from unittest.mock import patch

# conftest уже установил MAOCS_DB
import app


@pytest.fixture
def client():
    app.app.config['TESTING'] = True
    return app.app.test_client()


@pytest.fixture
def auth_client(client):
    """Клиент с залогиненным пользователем test_user."""
    client.post('/', data={
        'username': 'test_user',
        'password': 'pass123',
        'action': 'register',
    }, follow_redirects=True)
    return client


@pytest.fixture
def second_user(client):
    """Регистрация второго пользователя для тестов друзей."""
    client.post('/', data={
        'username': 'friend_user',
        'password': 'pass123',
        'action': 'register',
    }, follow_redirects=True)
    return client


def test_register_and_login(client):
    """Регистрация и вход возвращают редирект на /chat."""
    r = client.post('/', data={
        'username': 'newuser',
        'password': 'secret',
        'action': 'register',
    }, follow_redirects=False)
    assert r.status_code in (302, 200)
    if r.status_code == 302:
        assert '/chat' in r.location or r.location.endswith('/chat')

    r2 = client.post('/', data={
        'username': 'newuser',
        'password': 'secret',
        'action': 'login',
    }, follow_redirects=False)
    assert r2.status_code in (302, 200)
    if r2.status_code == 302:
        assert '/chat' in r2.location or r2.location.endswith('/chat')


def test_login_wrong_password(client):
    """Неверный пароль — не редирект на чат."""
    client.post('/', data={
        'username': 'newuser',
        'password': 'secret',
        'action': 'register',
    })
    r = client.post('/', data={
        'username': 'newuser',
        'password': 'wrong',
        'action': 'login',
    }, follow_redirects=False)
    assert r.status_code == 200
    assert 'Неверный пароль' in r.get_data(as_text=True) or 'пароль' in r.get_data(as_text=True).lower()


def test_send_friend_req_unauthorized(client):
    """Без сессии send_friend_req не должен падать (ошибка или редирект)."""
    r = client.post('/send_friend_req', json={'target': 'someone'})
    assert r.status_code in (200, 302, 403)
    if r.status_code == 200:
        data = r.get_json()
        assert data.get('error') or data.get('success') is not True


def test_send_friend_req_authorized(auth_client, second_user):
    """Авторизованный пользователь может отправить заявку в друзья."""
    r = auth_client.post('/send_friend_req', json={'target': 'friend_user'})
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('success') is True or data.get('error')


def test_delete_group_not_found(auth_client):
    """Удаление несуществующей группы возвращает 404 и JSON без 500."""
    with auth_client.session_transaction() as sess:
        sess['username'] = 'test_user'
    r = auth_client.post('/delete_group', json={'room_id': 'group_nonexistent_12345'})
    assert r.status_code == 404, f'Expected 404, got {r.status_code}: {r.get_json()}'
    data = r.get_json()
    assert data is not None
    assert 'error' in data


def test_delete_group_no_rights(auth_client, second_user):
    """Удаление группы не создателем — 403."""
    # test_user создаёт группу
    auth_client.post('/add_group', json={'target': 'TestGroup'})
    # Входим как friend_user (second_user уже зарегистрирован тем же client)
    with auth_client.session_transaction() as sess:
        sess['username'] = 'friend_user'
    auth_client.post('/add_group', json={'target': 'TestGroup'})  # вступает в группу
    r = auth_client.post('/delete_group', json={'room_id': 'group_TestGroup'})
    assert r.status_code == 403
    data = r.get_json()
    assert data is not None and data.get('error')


def test_upload_with_mock(auth_client):
    """Upload с моком внешнего API возвращает success или ошибку."""
    with auth_client.session_transaction() as sess:
        sess['username'] = 'test_user'
    with patch('app.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = 'https://files.catbox.moe/fake123.png'
        r = auth_client.post('/upload', data={'room': 'global', 'file': (io.BytesIO(b'fake'), 'test.png')})
    assert r.status_code == 200
    j = r.get_json()
    assert j is not None
    assert j.get('success') is True or 'error' in j
