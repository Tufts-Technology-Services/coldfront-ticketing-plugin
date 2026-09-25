import pytest


@pytest.fixture
def requestor(django_user_model):
    """The user who submits a request. Their username is their UTLN."""
    return django_user_model.objects.create_user(username="jdoe01", email="jdoe01@example.edu", password="pw")


@pytest.fixture
def pi(django_user_model):
    return django_user_model.objects.create_user(username="asmith02", email="asmith02@example.edu", password="pw")
