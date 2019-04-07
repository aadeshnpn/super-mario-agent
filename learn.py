import multiprocessing as mp

import torch
from tqdm import tqdm

from environment import MultiprocessEnvironment
from experience import ExperienceStorage
from policy import RecurrentPolicy
from ppo import PPOAgent


def learn(num_envs: int,
          device: torch.device,  # CUDA or CPU
          total_steps: int = 2048 * 8 * 640,
          steps_per_update: int = 2048,
          recurrent_hidden_state_size: int = 384,
          discount=0.99,
          gae_lambda=0.95,
          save_interval=64):
    envs = MultiprocessEnvironment(num_envs=num_envs)
    actor_critic = RecurrentPolicy(state_frame_channels=envs.observation_shape[0],
                                   action_space_size=envs.action_space_size,
                                   hidden_layer_size=recurrent_hidden_state_size,
                                   device=device)
    experience_storage = ExperienceStorage(steps_per_update,
                                           num_envs,
                                           envs.observation_shape,
                                           recurrent_hidden_state_size,
                                           device)
    agent = PPOAgent(actor_critic)

    initial_observations = envs.reset()
    experience_storage.insert_initial_observations(initial_observations)

    num_updates = total_steps // (num_envs * steps_per_update)

    for update_step in tqdm(range(num_updates)):
        for step in range(steps_per_update):
            with torch.no_grad():
                actor_input = experience_storage.get_actor_input(step)
                (values,
                 actions,
                 action_log_probs,
                 _,  # Action disribution entropy is not needed.
                 recurrent_hidden_states) = actor_critic.act(*actor_input)

                observations, rewards, done_values, _ = envs.step(actions)
                masks = 1 - done_values
                experience_storage.insert(observations,
                                          actions,
                                          action_log_probs,
                                          rewards,
                                          values,
                                          masks,
                                          recurrent_hidden_states)
        with torch.no_grad():
            critic_input = experience_storage.get_critic_input()
            next_value = actor_critic.value(*critic_input)
            experience_storage.compute_returns(next_value,
                                               discount=discount,
                                               gae_lambda=gae_lambda)

        losses = agent.update(experience_storage)

        print(experience_storage.rewards.mean(),
              experience_storage.rewards.std())

        save_model = (update_step % save_interval) == (save_interval - 1)
        if save_model:
            model_path = 'models/model_{}.bin'.format(update_step + 1)
            torch.save(actor_critic.state_dict(), model_path)


if __name__ == '__main__':
    cpu_count = mp.cpu_count()
    device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    learn(num_envs=cpu_count, device=device)
